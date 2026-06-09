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
