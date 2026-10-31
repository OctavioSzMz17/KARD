"""
Endpoints de suscripción (Stripe).

  POST /api/v1/billing/checkout  🔒 crea la sesión de pago del plan elegido
  GET  /api/v1/billing/plan      🔒 estado de la suscripción del negocio
  POST /api/v1/billing/webhook   🌐 avisos de Stripe (verificados por firma)

El webhook es el único sin autenticación: lo llama Stripe, que no tiene
sesión en KARD. Lo que lo protege es la firma criptográfica del propio
Stripe, verificada en el servicio — sin ella cualquiera podría activar
suscripciones mandando un POST.
"""

from fastapi import APIRouter, Depends, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from billing.models import CheckoutRequest, CheckoutResponse, PlanInfo
from billing.service import BillingService
from core.dependencies import CurrentUser, get_current_user
from db.database import get_db

router = APIRouter()


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_200_OK,
    summary="Create a Stripe Checkout Session for the chosen plan",
)
def create_checkout(
    payload: CheckoutRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CheckoutResponse:
    """
    Devuelve la URL de Stripe a la que redirigir al negocio.

    El tenant sale del token, nunca del cuerpo: de otro modo un negocio
    podría contratar un plan a nombre de otro.
    """
    return BillingService(db).create_checkout_session(payload.plan, current_user)


@router.get(
    "/plan",
    response_model=PlanInfo,
    summary="Subscription state of the authenticated business",
)
def get_plan(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PlanInfo:
    return BillingService(db).get_plan_info(current_user)


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Stripe webhook receiver (signature-verified)",
    include_in_schema=False,
)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """
    Recibe los avisos de Stripe.

    Se lee el cuerpo CRUDO, sin parsear: la firma se calcula sobre los bytes
    exactos que Stripe envió, y cualquier reserialización —aunque produzca un
    JSON equivalente— la invalidaría.

    Siempre responde 200 cuando el evento se procesó o se reconoció como
    repetido; así Stripe deja de reintentarlo.
    """
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    # El trabajo de base de datos es síncrono: en un hilo, para no congelar el
    # bucle de eventos (y con él a las demás peticiones) mientras Neon responde.
    return await run_in_threadpool(BillingService(db).handle_webhook, payload, signature)
