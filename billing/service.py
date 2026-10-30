"""
Suscripciones: el negocio le paga a KARD una mensualidad por la plataforma.

Cómo está repartida la verdad:

  · **Stripe** manda sobre el cobro — importes, facturas, reintentos y
    tarjetas viven allá y no se replican aquí.
  · **KARD** guarda solo el espejo mínimo (`subscription_status`, `plan`,
    `current_period_end`) para decidir si un negocio tiene acceso sin
    llamar a la API de Stripe en cada request.

El flujo:

  1. El negocio elige plan → se crea una Checkout Session y se le devuelve
     la URL a la que redirigirlo.
  2. Paga en Stripe (fuera de KARD: nunca tocamos datos de tarjeta).
  3. Stripe avisa por webhook → aquí se actualiza el espejo.

El webhook es la única fuente que mueve el estado. No se confía en que el
usuario regrese a la URL de éxito: puede cerrar la pestaña, y el pago
habría ocurrido igual.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from billing.models import CheckoutResponse, PlanInfo
from core.config import settings
from core.dependencies import CurrentUser
from core.exceptions import BillingUnavailableError, TenantNotFoundError
from db.models import StripeEvent, Tenant

logger = logging.getLogger("kard.billing")

# Estados de Stripe que dan derecho a usar la plataforma. `past_due` entra a
# propósito: la renovación falló pero el periodo anterior está pagado, y
# cortarle el acceso a un negocio que quizá solo cambió de tarjeta sería
# desproporcionado. Stripe reintenta durante días antes de pasar a `unpaid`.
ACTIVE_STATUSES = {"trialing", "active", "past_due"}


class BillingService:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Alta de suscripción
    # ------------------------------------------------------------------
    def create_checkout_session(self, plan: str, current_user: CurrentUser) -> CheckoutResponse:
        """
        Crea la sesión de pago de Stripe y devuelve su URL.

        El `tenant_id` sale del token, nunca del cuerpo: si no, un negocio
        podría contratar un plan a nombre de otro.
        """
        stripe = self._stripe()
        tenant = self._require_tenant(current_user.tenant_id)

        price_id = settings.stripe_price_by_plan.get(plan)
        if not price_id:
            raise BillingUnavailableError(f"El plan '{plan}' no tiene precio configurado")

        customer_id = self._ensure_customer(stripe, tenant)

        session = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{settings.APP_URL}/dashboard?suscripcion=ok",
            cancel_url=f"{settings.APP_URL}/dashboard?suscripcion=cancelada",
            # El tenant viaja en los metadatos para poder identificarlo al
            # recibir el webhook, que llega sin sesión ni token.
            metadata={"tenant_id": tenant.id, "plan": plan},
            subscription_data={"metadata": {"tenant_id": tenant.id, "plan": plan}},
        )

        logger.info("Checkout creado: tenant=%s plan=%s session=%s", tenant.id, plan, session.id)
        return CheckoutResponse(checkout_url=session.url, plan=plan)

    def _ensure_customer(self, stripe, tenant: Tenant) -> str:
        """
        Devuelve el cliente de Stripe del negocio, creándolo la primera vez.

        Se guarda en la base para que un segundo pago no cree un cliente
        duplicado y parta el historial de facturación en dos.
        """
        if tenant.stripe_customer_id:
            return tenant.stripe_customer_id

        customer = stripe.Customer.create(
            email=tenant.email,
            name=tenant.business_name,
            metadata={"tenant_id": tenant.id},
        )
        tenant.stripe_customer_id = customer.id
        self.db.commit()
        logger.info("Cliente de Stripe creado para tenant=%s: %s", tenant.id, customer.id)
        return customer.id

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------
    def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """
        Verifica la firma de Stripe y aplica el evento.

        La firma es lo que impide que cualquiera active suscripciones
        mandando un POST: sin ella el endpoint sería público y crédulo.
        """
        stripe = self._stripe()
        if not settings.STRIPE_WEBHOOK_SECRET:
            raise BillingUnavailableError("Falta STRIPE_WEBHOOK_SECRET")

        try:
            event = stripe.Webhook.construct_event(
                payload, signature, settings.STRIPE_WEBHOOK_SECRET
            )
        except Exception as exc:
            logger.warning("Webhook con firma inválida: %s", exc)
            raise BillingUnavailableError("Firma de webhook inválida") from exc

        # Stripe reintenta hasta recibir un 2xx y puede repetir un evento.
        # Registrarlo primero vuelve idempotente todo lo que sigue.
        if self.db.get(StripeEvent, event["id"]) is not None:
            logger.info("Evento repetido, se ignora: %s", event["id"])
            return {"received": True, "duplicate": True}

        self.db.add(StripeEvent(id=event["id"], type=event["type"]))

        handler = {
            "checkout.session.completed": self._on_checkout_completed,
            "customer.subscription.created": self._on_subscription_changed,
            "customer.subscription.updated": self._on_subscription_changed,
            "customer.subscription.deleted": self._on_subscription_deleted,
            "invoice.payment_failed": self._on_payment_failed,
        }.get(event["type"])

        if handler:
            handler(event["data"]["object"])
        else:
            # Se registra igual para no reprocesarlo si Stripe lo reintenta.
            logger.debug("Evento sin manejador: %s", event["type"])

        self.db.commit()
        return {"received": True, "type": event["type"]}

    def _on_checkout_completed(self, session: dict) -> None:
        """El negocio terminó de pagar. El detalle real llega en el evento de suscripción."""
        tenant = self._tenant_from(session)
        if tenant is None:
            return
        tenant.stripe_subscription_id = session.get("subscription") or tenant.stripe_subscription_id
        if session.get("customer"):
            tenant.stripe_customer_id = session["customer"]
        logger.info("Checkout completado para tenant=%s", tenant.id)

    def _on_subscription_changed(self, sub: dict) -> None:
        """Alta, renovación, cambio de plan o de estado: Stripe manda el objeto completo."""
        tenant = self._tenant_from(sub)
        if tenant is None:
            return

        tenant.stripe_subscription_id = sub["id"]
        tenant.subscription_status = sub["status"]
        tenant.plan = self._plan_from(sub) or tenant.plan

        period_end = sub.get("current_period_end")
        if period_end:
            tenant.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)

        logger.info(
            "Suscripción actualizada: tenant=%s plan=%s estado=%s",
            tenant.id, tenant.plan, tenant.subscription_status,
        )

    def _on_subscription_deleted(self, sub: dict) -> None:
        tenant = self._tenant_from(sub)
        if tenant is None:
            return
        tenant.subscription_status = "canceled"
        logger.info("Suscripción cancelada para tenant=%s", tenant.id)

    def _on_payment_failed(self, invoice: dict) -> None:
        """
        No se cancela nada aquí: Stripe reintenta durante días y él decide
        cuándo la suscripción pasa a `unpaid`. Forzar el estado desde aquí
        adelantaría el corte y pelearía con el evento que llegue después.
        """
        tenant = self._tenant_from(invoice)
        if tenant is None:
            return
        logger.warning("Pago fallido para tenant=%s, Stripe reintentará", tenant.id)

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------
    def get_plan_info(self, current_user: CurrentUser) -> PlanInfo:
        tenant = self._require_tenant(current_user.tenant_id)
        return PlanInfo(
            plan=tenant.plan,
            subscription_status=tenant.subscription_status,
            current_period_end=(
                tenant.current_period_end.isoformat() if tenant.current_period_end else None
            ),
            has_access=tenant.subscription_status in ACTIVE_STATUSES,
        )

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------
    @staticmethod
    def _stripe():
        """
        Importa y configura el SDK solo cuando hace falta.

        Diferirlo permite que la plataforma arranque y funcione sin claves de
        Stripe: solo los endpoints de cobro fallan, con un mensaje claro.
        """
        if not settings.billing_enabled:
            raise BillingUnavailableError("El cobro no está configurado en este entorno")
        import stripe as stripe_sdk

        stripe_sdk.api_key = settings.STRIPE_SECRET_KEY
        return stripe_sdk

    def _require_tenant(self, tenant_id: str) -> Tenant:
        tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise TenantNotFoundError(tenant_id)
        return tenant

    def _tenant_from(self, obj: dict) -> Optional[Tenant]:
        """
        Localiza el negocio de un objeto de Stripe.

        Se busca primero por los metadatos (que ponemos nosotros al crear la
        sesión) y se cae al `customer` como respaldo, porque no todos los
        objetos arrastran los metadatos de la suscripción.
        """
        tenant_id = (obj.get("metadata") or {}).get("tenant_id")
        if tenant_id:
            tenant = self.db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                return tenant

        customer_id = obj.get("customer")
        if customer_id:
            tenant = self.db.query(Tenant).filter(Tenant.stripe_customer_id == customer_id).first()
            if tenant:
                return tenant

        logger.warning("Evento de Stripe sin negocio identificable: %s", obj.get("id"))
        return None

    @staticmethod
    def _plan_from(sub: dict) -> Optional[str]:
        """Deduce el plan desde los metadatos, o desde el precio contratado."""
        plan = (sub.get("metadata") or {}).get("plan")
        if plan:
            return plan

        items = (sub.get("items") or {}).get("data") or []
        if not items:
            return None
        price_id = (items[0].get("price") or {}).get("id")
        for nombre, pid in settings.stripe_price_by_plan.items():
            if pid and pid == price_id:
                return nombre
        return None
