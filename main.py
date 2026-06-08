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
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    TenantAccessError,
    TenantNotFoundError,
    UserCreationNotAllowedError,
)
from saasadmin.routes import router as saasadmin_router

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


@app.get("/health")
async def health_check():
    """Lightweight liveness probe used by Docker healthchecks and uptime checks."""
    return {"status": "ok", "env": settings.ENVIRONMENT}


app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(saasadmin_router, prefix="/api/v1/saasadmin", tags=["saasadmin"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
