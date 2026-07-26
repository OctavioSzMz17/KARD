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
