"""
Pruebas de suscripciones (Stripe).

Cubren las reglas que, si fallan, cobran de más, dan acceso gratis o
corrompen el estado del negocio:

  PS-08  Sin configuración de Stripe no se intenta cobrar, se avisa.
  PS-09  Un evento repetido de Stripe no se aplica dos veces.
  PS-10  El espejo del estado refleja lo que manda Stripe, no lo que
         supone KARD.

No tocan la red ni la base: el SDK de Stripe se sustituye por un doble.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from billing.service import ACTIVE_STATUSES, BillingService
from core.exceptions import BillingUnavailableError
from db.models import StripeEvent


class FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class FakeDB:
    """Sesión mínima: resuelve tenants y recuerda qué eventos se registraron."""

    def __init__(self, tenant=None, eventos_previos=None):
        self.tenant = tenant
        self.added = []
        self.commits = 0
        self._eventos = set(eventos_previos or [])

    def query(self, *a, **k):
        return FakeQuery(self.tenant)

    def get(self, model, pk):
        return StripeEvent(id=pk, type="x") if pk in self._eventos else None

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, StripeEvent):
            self._eventos.add(obj.id)

    def commit(self):
        self.commits += 1


def tenant_demo(**kw):
    base = dict(
        id="t_aaa", business_name="Sabor Norteño", email="admin@a.test",
        stripe_customer_id=None, stripe_subscription_id=None,
        subscription_status="none", plan=None, current_period_end=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ═══════════════════════════════════════════════════════════════════
# PS-08 · Sin configuración, no se intenta cobrar
# ═══════════════════════════════════════════════════════════════════


class TestSinConfiguracion:
    def test_falla_claro_si_no_hay_clave(self, monkeypatch):
        """
        El resto de la plataforma debe seguir funcionando sin Stripe; solo
        el cobro avisa que no está disponible.
        """
        from core import config as config_module
        monkeypatch.setattr(config_module.settings, "STRIPE_SECRET_KEY", "", raising=False)

        with pytest.raises(BillingUnavailableError):
            BillingService._stripe()


# ═══════════════════════════════════════════════════════════════════
# PS-09 · Idempotencia del webhook
# ═══════════════════════════════════════════════════════════════════


class TestEventoRepetido:
    """
    Stripe reintenta hasta recibir un 2xx y puede repetir un evento. Si el
    segundo intento volviera a aplicarse, un cambio de plan podría
    duplicarse o revertirse.
    """

    def _service_con_stripe_falso(self, monkeypatch, db, evento):
        from billing import service as mod

        class FakeWebhook:
            @staticmethod
            def construct_event(payload, sig, secret):
                return evento

        monkeypatch.setattr(mod.BillingService, "_stripe",
                            staticmethod(lambda: SimpleNamespace(Webhook=FakeWebhook)))
        monkeypatch.setattr(mod.settings, "STRIPE_WEBHOOK_SECRET", "whsec_test", raising=False)
        return mod.BillingService(db)

    def test_el_primero_se_aplica(self, monkeypatch):
        tenant = tenant_demo()
        db = FakeDB(tenant)
        evento = {
            "id": "evt_1", "type": "customer.subscription.updated",
            "data": {"object": {
                "id": "sub_1", "status": "active", "customer": "cus_1",
                "metadata": {"tenant_id": "t_aaa", "plan": "pro"},
                "current_period_end": 1800000000,
            }},
        }
        svc = self._service_con_stripe_falso(monkeypatch, db, evento)

        r = svc.handle_webhook(b"{}", "firma")

        assert r["received"] is True
        assert r.get("duplicate") is not True
        assert tenant.subscription_status == "active"
        assert tenant.plan == "pro"

    def test_el_repetido_se_ignora(self, monkeypatch):
        tenant = tenant_demo(subscription_status="active", plan="pro")
        db = FakeDB(tenant, eventos_previos={"evt_1"})
        evento = {
            "id": "evt_1", "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_1", "customer": "cus_1",
                                "metadata": {"tenant_id": "t_aaa"}}},
        }
        svc = self._service_con_stripe_falso(monkeypatch, db, evento)

        r = svc.handle_webhook(b"{}", "firma")

        assert r["duplicate"] is True
        # El estado NO cambió aunque el evento era de cancelación.
        assert tenant.subscription_status == "active"

    def test_firma_invalida_no_toca_nada(self, monkeypatch):
        from billing import service as mod

        class FakeWebhook:
            @staticmethod
            def construct_event(payload, sig, secret):
                raise ValueError("bad signature")

        tenant = tenant_demo(subscription_status="active")
        db = FakeDB(tenant)
        monkeypatch.setattr(mod.BillingService, "_stripe",
                            staticmethod(lambda: SimpleNamespace(Webhook=FakeWebhook)))
        monkeypatch.setattr(mod.settings, "STRIPE_WEBHOOK_SECRET", "whsec_test", raising=False)

        with pytest.raises(BillingUnavailableError):
            mod.BillingService(db).handle_webhook(b"{}", "firma-falsa")

        assert tenant.subscription_status == "active"
        assert db.commits == 0


# ═══════════════════════════════════════════════════════════════════
# PS-10 · El espejo refleja a Stripe
# ═══════════════════════════════════════════════════════════════════


class TestEspejoDelEstado:
    def test_guarda_estado_plan_y_periodo(self):
        tenant = tenant_demo()
        svc = BillingService(FakeDB(tenant))

        svc._on_subscription_changed({
            "id": "sub_9", "status": "trialing", "customer": "cus_9",
            "metadata": {"tenant_id": "t_aaa", "plan": "starter"},
            "current_period_end": 1800000000,
        })

        assert tenant.stripe_subscription_id == "sub_9"
        assert tenant.subscription_status == "trialing"
        assert tenant.plan == "starter"
        assert tenant.current_period_end == datetime.fromtimestamp(1800000000, tz=timezone.utc)

    def test_cancelacion_marca_canceled(self):
        tenant = tenant_demo(subscription_status="active", plan="pro")
        svc = BillingService(FakeDB(tenant))

        svc._on_subscription_deleted({"id": "sub_9", "customer": "cus_9",
                                      "metadata": {"tenant_id": "t_aaa"}})

        assert tenant.subscription_status == "canceled"

    def test_pago_fallido_no_corta_el_acceso(self):
        """
        Stripe reintenta durante días y decide él cuándo pasa a `unpaid`.
        Cortar aquí adelantaría el corte y pelearía con el evento siguiente.
        """
        tenant = tenant_demo(subscription_status="active", plan="pro")
        svc = BillingService(FakeDB(tenant))

        svc._on_payment_failed({"id": "in_1", "customer": "cus_9",
                                "metadata": {"tenant_id": "t_aaa"}})

        assert tenant.subscription_status == "active"

    def test_past_due_conserva_el_acceso(self):
        """La renovación falló pero el periodo anterior está pagado."""
        assert "past_due" in ACTIVE_STATUSES
        assert "canceled" not in ACTIVE_STATUSES
        assert "none" not in ACTIVE_STATUSES

    def test_evento_sin_negocio_no_truena(self):
        """Un evento de un cliente que no es nuestro se ignora, no rompe."""
        svc = BillingService(FakeDB(tenant=None))
        svc._on_subscription_changed({"id": "sub_x", "status": "active",
                                      "customer": "cus_ajeno", "metadata": {}})

    def test_deduce_el_plan_desde_el_precio(self, monkeypatch):
        """Si el evento no trae metadatos, el plan sale del precio contratado."""
        from billing import service as mod
        monkeypatch.setattr(
            type(mod.settings), "stripe_price_by_plan",
            property(lambda self: {"starter": "price_s", "pro": "price_p", "enterprise": ""}),
        )

        plan = BillingService._plan_from({
            "metadata": {},
            "items": {"data": [{"price": {"id": "price_p"}}]},
        })
        assert plan == "pro"
