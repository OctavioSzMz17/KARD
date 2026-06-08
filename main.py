"""
KARD — FastAPI application entrypoint.

Bootstraps the app, CORS, global exception handlers and the
`/health` endpoint. Feature routers are registered here in their
own branches (feature/auth, feature/products, ...) — none yet.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.config import settings
from core.exceptions import TenantAccessError

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


@app.exception_handler(TenantAccessError)
async def tenant_access_exception_handler(request: Request, exc: TenantAccessError):
    """Cross-tenant access attempts are rejected with 403 (Rules.md section 10)."""
    return JSONResponse(
        status_code=403,
        content={"success": False, "error": "Access denied"},
    )


@app.get("/health")
async def health_check():
    """Lightweight liveness probe used by Docker healthchecks and uptime checks."""
    return {"status": "ok", "env": settings.ENVIRONMENT}


# NOTE: Routers (auth, products, businessadmin, saasadmin, ...) are
# included in their respective feature branches, e.g.:
#   app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
