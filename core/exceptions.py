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


class UserNotFoundError(Exception):
    """Raised when a requested user does not exist."""


class InvalidCredentialsError(Exception):
    """Raised when login fails: unknown email, wrong password, or inactive account."""


class EmailAlreadyExistsError(Exception):
    """Raised on registration when the email is already associated with a tenant or user."""


class AccountLockedError(Exception):
    """Raised when an account is temporarily locked after too many failed login attempts."""
