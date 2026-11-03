"""
Custom domain exceptions (Rules.md section 10).

These are raised from the service/repository layers to signal business
rule violations or access issues. FastAPI exception handlers (registered
in main.py / each feature's routes) translate them into consistent
`{"success": False, "error": "..."}` HTTP responses without leaking
internal details to the client.
"""


class TenantAccessError(Exception):
    """Raised when a user attempts to access another tenant's data."""


class ProductNotFoundError(Exception):
    """Raised when a requested product does not exist for the tenant."""


class InsufficientStockError(Exception):
    """Raised when an inventory operation would drive stock below zero."""


class InvalidMetadataError(Exception):
    """Raised when product JSONB metadata fails schema validation."""


class DuplicateProductNameError(Exception):
    """Raised when a product name collides with an existing one for the same tenant."""


class UserNotFoundError(Exception):
    """Raised when a requested user does not exist."""


class InvalidCredentialsError(Exception):
    """Raised when login fails: unknown email, wrong password, or inactive account."""


class EmailAlreadyExistsError(Exception):
    """Raised on registration when the email is already associated with a tenant or user."""


class AccountLockedError(Exception):
    """Raised when an account is temporarily locked after too many failed login attempts."""


class TenantNotFoundError(Exception):
    """Raised when a referenced tenant_id does not exist or is not active."""


class ImageNotFoundError(Exception):
    """Raised when a requested image does not exist for the tenant/owner."""


class UserCreationNotAllowedError(Exception):
    """
    Raised when a user-creation request violates the role/ownership rules
    from US-018 (e.g. staff trying to create accounts, a business_admin
    trying to mint a saas_admin, or the one-time saas_admin bootstrap
    being attempted while a platform owner already exists).
    """


class TenantAlreadyExistsError(Exception):
    """Raised when self-service registration is attempted with an email already in use by a tenant."""


class StorefrontNotFoundError(Exception):
    """Raised when a slug resolves to no tenant, or the tenant's storefront is not public."""


class PlanLimitExceededError(Exception):
    """Raised when a tenant tries to exceed the resource limit of their current plan."""


class ConsumerAlreadyExistsError(Exception):
    """Raised when a consumer tries to register with an email already in use."""


class ConsumerNotFoundError(Exception):
    """Raised when a consumer_id does not exist or the account is inactive."""


class CartEmptyError(Exception):
    """Raised when attempting to create an order from an empty cart."""


class CartUnavailableError(Exception):
    """Raised when Redis is unreachable and cart operations cannot be completed."""


class OrderNotFoundError(Exception):
    """Raised when an order does not exist or belongs to a different consumer."""


class InvalidRecipeError(Exception):
    """
    Raised when a recipe item is invalid: the parent is not a 'final' product,
    the ingredient is not an 'insumo', or a product references itself.
    """


class OrderStockConflictError(Exception):
    """
    Raised at checkout when one or more products (or their ingredients) lack
    stock. Carries the structured `unavailable` list so the API can return a
    machine-readable 409 the frontend can interpret to ask the user whether
    to continue without those items or cancel the whole order.
    """

    def __init__(self, unavailable: list[dict], all_skipped: bool = False):
        self.unavailable = unavailable
        self.all_skipped = all_skipped
        super().__init__("insufficient stock for one or more items")


class BillingUnavailableError(Exception):
    """
    El cobro no se puede ejecutar: faltan claves de Stripe, el plan no tiene
    precio configurado, o la firma del webhook no es válida.

    Es distinto de un error del negocio: aquí la petición puede ser correcta
    y aun así no haber manera de cobrar, así que se responde 503 y no 400.
    """


# ── Caja y punto de venta ───────────────────────────────────────────────


class CashSessionAlreadyOpenError(Exception):
    """Se intentó abrir la caja cuando el negocio ya tiene un turno abierto."""


class CashClosedError(Exception):
    """
    Se intentó cobrar en el mostrador sin tener la caja abierta.

    Sin turno abierto la venta no pertenecería a ningún corte, y el efectivo
    esperado al cerrar no cuadraría con lo que realmente entró.
    """


class CashSessionNotFoundError(Exception):
    """El turno de caja pedido no existe o es de otro negocio."""


class InvalidDiscountError(Exception):
    """El descuento no se puede aplicar (por ejemplo, mayor que el subtotal)."""


class InsufficientPaymentError(Exception):
    """El efectivo recibido no alcanza para cubrir el total de la venta."""


class IdempotencyConflictError(Exception):
    """
    La misma llave de idempotencia llegó con una petición distinta.

    Un reintento legítimo repite la petición idéntica; si cambia el contenido
    (otros artículos, otro importe, otra persona cobrando), contestar con la
    venta anterior engañaría al cajero sobre lo que acaba de cobrar.
    """


class DuplicateProductCodeError(Exception):
    """El SKU o el código de barras ya lo usa otro producto vigente del negocio."""

    def __init__(self, field: str, value: str):
        super().__init__(f"{field}={value}")
        self.field = field
        self.value = value


class TeamHierarchyError(Exception):
    """
    Alguien intenta registrar o modificar a una persona de su mismo nivel o
    superior, o dar un puesto que no le corresponde dar (por ejemplo, un
    subgerente nombrando a otro subgerente).
    """


class DeviceNotAuthorizedError(Exception):
    """Cobrar (u otra acción de caja) desde un equipo que la gerencia no autorizó hoy, o cuya autorización venció."""


class ChallengeNotFoundError(Exception):
    """El QR no existe, ya se usó o no corresponde a quien lo consulta. No se distingue, a propósito."""


class ChallengeExpiredError(Exception):
    """El QR caducó: la computadora debe mostrar uno nuevo."""


class WrongVerificationCodeError(Exception):
    """El código de 4 dígitos no coincide con el que muestra la computadora."""


class PermissionDeniedError(Exception):
    """Falta el permiso para esta acción concreta (la ruta ya dejó pasar, el servicio decide el caso)."""


class TooManyChallengesError(Exception):
    """Demasiados QR pedidos desde la misma dirección en poco tiempo."""
