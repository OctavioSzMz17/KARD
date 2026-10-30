"""Esquemas del módulo de suscripciones."""

from typing import Literal, Optional

from pydantic import BaseModel

Plan = Literal["starter", "pro", "enterprise"]


class CheckoutRequest(BaseModel):
    """Payload de POST /api/v1/billing/checkout."""

    plan: Plan


class CheckoutResponse(BaseModel):
    """URL de Stripe a la que hay que redirigir al negocio para que pague."""

    checkout_url: str
    plan: str


class PlanInfo(BaseModel):
    """
    Estado de la suscripción del negocio autenticado.

    `has_access` resume en un booleano lo que el frontend necesita decidir —
    si deja entrar o muestra la pantalla de contratar— sin tener que conocer
    la lista de estados de Stripe.
    """

    plan: Optional[str] = None
    subscription_status: str
    current_period_end: Optional[str] = None
    has_access: bool
