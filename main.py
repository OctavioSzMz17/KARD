"""
KARD — FastAPI application entrypoint.

Bootstraps the app, logging, CORS, global exception handlers, the
`/health` endpoint, and feature routers.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from audit_logging.logger import setup_logging
from auth.routes import router as auth_router
from core.config import settings
from core.exceptions import (
    AccountLockedError,
    BillingUnavailableError,
    CartEmptyError,
    CartUnavailableError,
    CashClosedError,
    ChallengeExpiredError,
    ChallengeNotFoundError,
    DeviceNotAuthorizedError,
    PermissionDeniedError,
    TooManyChallengesError,
    WrongVerificationCodeError,
    CashSessionAlreadyOpenError,
    CashSessionNotFoundError,
    ConsumerAlreadyExistsError,
    ConsumerNotFoundError,
    DuplicateProductCodeError,
    DuplicateProductNameError,
    EmailAlreadyExistsError,
    ImageNotFoundError,
    InsufficientPaymentError,
    InsufficientStockError,
    InvalidCredentialsError,
    InvalidDiscountError,
    InvalidMetadataError,
    InvalidRecipeError,
    OrderNotFoundError,
    OrderStockConflictError,
    PlanLimitExceededError,
    ProductNotFoundError,
    StorefrontNotFoundError,
    TenantAccessError,
    TenantAlreadyExistsError,
    TeamHierarchyError,
    TenantNotFoundError,
    UserCreationNotAllowedError,
    UserNotFoundError,
)
from billing.routes import router as billing_router
from cash.routes import router as cash_router
from cart.routes import router as cart_router
from consumers.routes import router as consumers_router
from explore.routes import router as explore_router
from orders.routes import router as orders_router
from recipes.routes import router as recipes_router
from images.routes import router as images_router
from products.routes import router as products_router
from saasadmin.routes import router as saasadmin_router
from devices.routes import router as devices_router
from team.routes import router as team_router
from settings.routes import router as settings_router
from storefront.routes import router as storefront_router

setup_logging()

app = FastAPI(title="KARD", version="1.0.0")

# CORS — only the configured frontend origin(s), credentials allowed
# (Rules.md section 2.6)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error(message: str) -> dict:
    """Shared error envelope (Rules.md section 10): `{"success": false, "error": "..."}`."""
    return {"success": False, "error": message}


@app.exception_handler(TenantAccessError)
async def tenant_access_exception_handler(request: Request, exc: TenantAccessError):
    """Cross-tenant access attempts are rejected with 403 (Rules.md section 10)."""
    return JSONResponse(status_code=403, content=_error("Access denied"))


@app.exception_handler(InvalidCredentialsError)
async def invalid_credentials_exception_handler(request: Request, exc: InvalidCredentialsError):
    """Unknown email, wrong password, or inactive account → 401 (never reveal which)."""
    return JSONResponse(status_code=401, content=_error("Invalid email or password"))


@app.exception_handler(EmailAlreadyExistsError)
async def email_already_exists_exception_handler(request: Request, exc: EmailAlreadyExistsError):
    """Duplicate email on registration → 400."""
    return JSONResponse(status_code=400, content=_error("Email is already registered"))


@app.exception_handler(TenantAlreadyExistsError)
async def tenant_already_exists_exception_handler(request: Request, exc: TenantAlreadyExistsError):
    """Email already used by a tenant → 400."""
    return JSONResponse(status_code=400, content=_error("Email is already registered"))


@app.exception_handler(AccountLockedError)
async def account_locked_exception_handler(request: Request, exc: AccountLockedError):
    """Too many failed login attempts → 403, account temporarily locked."""
    return JSONResponse(
        status_code=403,
        content=_error("Account temporarily locked due to too many failed login attempts"),
    )


@app.exception_handler(TenantNotFoundError)
async def tenant_not_found_exception_handler(request: Request, exc: TenantNotFoundError):
    """Referenced tenant_id doesn't exist or isn't active → 404 (US-018)."""
    return JSONResponse(status_code=404, content=_error("Tenant not found"))


@app.exception_handler(UserCreationNotAllowedError)
async def user_creation_not_allowed_exception_handler(request: Request, exc: UserCreationNotAllowedError):
    """Role/ownership rules for user creation violated → 403 (US-018)."""
    return JSONResponse(status_code=403, content=_error(str(exc)))


@app.exception_handler(ProductNotFoundError)
async def product_not_found_exception_handler(request: Request, exc: ProductNotFoundError):
    """Unknown id, soft-deleted, or belongs to another tenant → 404 (Rules.md section 7.5)."""
    return JSONResponse(status_code=404, content=_error("Product not found"))


@app.exception_handler(DuplicateProductNameError)
async def duplicate_product_name_exception_handler(request: Request, exc: DuplicateProductNameError):
    """Product name must be unique per tenant (`uq_product_name_tenant`) → 400."""
    return JSONResponse(status_code=400, content=_error("A product with this name already exists"))


@app.exception_handler(InsufficientStockError)
async def insufficient_stock_exception_handler(request: Request, exc: InsufficientStockError):
    """Selling more than is in stock would drive inventory below zero → 400."""
    return JSONResponse(status_code=400, content=_error(str(exc)))


@app.exception_handler(InvalidMetadataError)
async def invalid_metadata_exception_handler(request: Request, exc: InvalidMetadataError):
    """Product JSONB metadata doesn't match the tenant's business_type schema → 422."""
    return JSONResponse(status_code=422, content=_error(str(exc)))


@app.exception_handler(ImageNotFoundError)
async def image_not_found_exception_handler(request: Request, exc: ImageNotFoundError):
    """Unknown image id, belongs to a different product, or different tenant → 404."""
    return JSONResponse(status_code=404, content=_error("Image not found"))


@app.exception_handler(StorefrontNotFoundError)
async def storefront_not_found_exception_handler(request: Request, exc: StorefrontNotFoundError):
    """Slug not found, tenant not public, or tenant inactive → 404 (indistinguishable by design)."""
    return JSONResponse(status_code=404, content=_error("Storefront not found"))


@app.exception_handler(BillingUnavailableError)
async def billing_unavailable_exception_handler(request: Request, exc: BillingUnavailableError):
    """
    Falta configuración de Stripe o la firma del webhook no es válida → 503.

    No es 400: la petición puede ser correcta y aun así no haber manera de
    cobrar, así que el problema es del servicio, no de quien llama.
    """
    return JSONResponse(status_code=503, content=_error(str(exc)))


@app.exception_handler(PlanLimitExceededError)
async def plan_limit_exceeded_exception_handler(request: Request, exc: PlanLimitExceededError):
    return JSONResponse(status_code=402, content=_error(str(exc)))


@app.exception_handler(ConsumerAlreadyExistsError)
async def consumer_already_exists_exception_handler(request: Request, exc: ConsumerAlreadyExistsError):
    return JSONResponse(status_code=400, content=_error("Email is already registered"))


@app.exception_handler(ConsumerNotFoundError)
async def consumer_not_found_exception_handler(request: Request, exc: ConsumerNotFoundError):
    return JSONResponse(status_code=404, content=_error("Consumer not found"))


@app.exception_handler(CartEmptyError)
async def cart_empty_exception_handler(request: Request, exc: CartEmptyError):
    return JSONResponse(status_code=400, content=_error("El carrito está vacío"))


@app.exception_handler(CartUnavailableError)
async def cart_unavailable_exception_handler(request: Request, exc: CartUnavailableError):
    return JSONResponse(status_code=503, content=_error("Cart service temporarily unavailable"))


@app.exception_handler(OrderNotFoundError)
async def order_not_found_exception_handler(request: Request, exc: OrderNotFoundError):
    return JSONResponse(status_code=404, content=_error("Order not found"))


@app.exception_handler(InvalidRecipeError)
async def invalid_recipe_exception_handler(request: Request, exc: InvalidRecipeError):
    """Recipe item with wrong product types or self-reference → 400."""
    return JSONResponse(status_code=400, content=_error(str(exc)))


@app.exception_handler(OrderStockConflictError)
async def order_stock_conflict_exception_handler(request: Request, exc: OrderStockConflictError):
    """
    Checkout stock conflict → 409 with a machine-readable body the frontend
    uses to ask the user: continue without these items, or cancel the order.

    Shape:
      {
        "success": false,
        "code": "INSUFFICIENT_STOCK",
        "error": "<mensaje legible>",
        "details": { "all_unavailable": bool, "unavailable": [ ... ] }
      }
    """
    if exc.all_skipped:
        message = "Ningún producto del pedido tiene stock suficiente"
    else:
        message = "Algunos productos no tienen stock suficiente"
    return JSONResponse(
        status_code=409,
        content={
            "success": False,
            "code": "INSUFFICIENT_STOCK",
            "error": message,
            "details": {
                "all_unavailable": exc.all_skipped,
                "unavailable": exc.unavailable,
            },
        },
    )


def _coded(status_code: int, code: str, message: str) -> JSONResponse:
    """Error con `code` legible por máquina: el frontend decide qué mostrar según él."""
    return JSONResponse(status_code=status_code, content={"success": False, "code": code, "error": message})


@app.exception_handler(DeviceNotAuthorizedError)
async def device_not_authorized_exception_handler(request: Request, exc: DeviceNotAuthorizedError):
    """Cobrar o abrir sesión desde un equipo que la gerencia no autorizó hoy → 403."""
    return _coded(403, "DEVICE_NOT_AUTHORIZED", str(exc) or "Este equipo no está autorizado hoy")


@app.exception_handler(ChallengeNotFoundError)
async def challenge_not_found_exception_handler(request: Request, exc: ChallengeNotFoundError):
    """El QR no existe, ya se usó o es de otro negocio: no se distingue → 404."""
    return _coded(404, "CHALLENGE_NOT_FOUND", "Este código ya no es válido")


@app.exception_handler(ChallengeExpiredError)
async def challenge_expired_exception_handler(request: Request, exc: ChallengeExpiredError):
    """El QR caducó: la computadora debe mostrar uno nuevo → 410."""
    return _coded(410, "CHALLENGE_EXPIRED", "El código caducó; pide uno nuevo")


@app.exception_handler(WrongVerificationCodeError)
async def wrong_code_exception_handler(request: Request, exc: WrongVerificationCodeError):
    """El código de 4 dígitos no coincide con el de la pantalla → 400."""
    return _coded(400, "WRONG_CODE", "El código no coincide con el de la pantalla")


@app.exception_handler(PermissionDeniedError)
async def permission_denied_exception_handler(request: Request, exc: PermissionDeniedError):
    """Falta permiso para esta acción concreta → 403."""
    return _coded(403, "FORBIDDEN", str(exc) or "No tienes permiso para esta operación")


@app.exception_handler(TooManyChallengesError)
async def too_many_challenges_exception_handler(request: Request, exc: TooManyChallengesError):
    """Demasiados QR pedidos en poco tiempo desde esta dirección → 429."""
    return _coded(429, "TOO_MANY_REQUESTS", "Demasiados intentos; espera unos minutos")


@app.exception_handler(TeamHierarchyError)
async def team_hierarchy_exception_handler(request: Request, exc: TeamHierarchyError):
    """Actuar sobre alguien de su nivel o superior, o dar un puesto que no le toca → 403."""
    return _coded(403, "TEAM_HIERARCHY", str(exc))


@app.exception_handler(UserNotFoundError)
async def user_not_found_exception_handler(request: Request, exc: UserNotFoundError):
    """La persona no existe en este negocio (o es de otro: no se distingue) → 404."""
    return _coded(404, "USER_NOT_FOUND", "No existe esa persona en tu equipo")


@app.exception_handler(CashClosedError)
async def cash_closed_exception_handler(request: Request, exc: CashClosedError):
    """Cobrar sin caja abierta → 409; el punto de venta responde pidiendo abrirla."""
    return _coded(409, "CASH_CLOSED", "Abre la caja antes de cobrar")


@app.exception_handler(CashSessionAlreadyOpenError)
async def cash_already_open_exception_handler(request: Request, exc: CashSessionAlreadyOpenError):
    return _coded(409, "CASH_ALREADY_OPEN", "Ya hay una caja abierta")


@app.exception_handler(CashSessionNotFoundError)
async def cash_not_found_exception_handler(request: Request, exc: CashSessionNotFoundError):
    return _coded(404, "CASH_SESSION_NOT_FOUND", "Turno de caja no encontrado")


@app.exception_handler(InvalidDiscountError)
async def invalid_discount_exception_handler(request: Request, exc: InvalidDiscountError):
    return _coded(400, "INVALID_DISCOUNT", str(exc))


@app.exception_handler(InsufficientPaymentError)
async def insufficient_payment_exception_handler(request: Request, exc: InsufficientPaymentError):
    return _coded(400, "INSUFFICIENT_PAYMENT", str(exc))


@app.exception_handler(DuplicateProductCodeError)
async def duplicate_product_code_exception_handler(request: Request, exc: DuplicateProductCodeError):
    """SKU o código de barras repetido entre los productos vigentes del negocio → 400."""
    etiqueta = "El SKU" if exc.field == "sku" else "El código de barras"
    return _coded(400, "DUPLICATE_PRODUCT_CODE", f"{etiqueta} '{exc.value}' ya lo usa otro producto")


@app.get("/health")
async def health_check():
    """Lightweight liveness probe used by Docker healthchecks and uptime checks."""
    return {"status": "ok", "env": settings.ENVIRONMENT}


app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(saasadmin_router, prefix="/api/v1/saasadmin", tags=["saasadmin"])
app.include_router(products_router, prefix="/api/v1/products", tags=["products"])
app.include_router(images_router, prefix="/api/v1/products", tags=["images"])
app.include_router(recipes_router, prefix="/api/v1/products", tags=["recipes"])
app.include_router(storefront_router, prefix="/s", tags=["storefront"])
app.include_router(consumers_router, prefix="/api/v1/consumers", tags=["consumers"])
app.include_router(billing_router, prefix="/api/v1/billing", tags=["billing"])
app.include_router(explore_router, prefix="/api/v1/explore", tags=["explore"])
app.include_router(cart_router, prefix="/api/v1/cart", tags=["cart"])
app.include_router(orders_router, prefix="/api/v1/orders", tags=["orders"])
app.include_router(cash_router, prefix="/api/v1/cash", tags=["cash"])
app.include_router(settings_router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(team_router, prefix="/api/v1/team", tags=["team"])
app.include_router(devices_router, prefix="/api/v1/device-auth", tags=["device-auth"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
