"""
Shared FastAPI dependencies: authentication and tenant context.

Rules.md section 2 requires every protected route to depend on
`get_current_user` (and, transitively, on tenant extraction from the
JWT — never from the request body/path/query).
"""

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Resolve the authenticated user from the bearer token.

    TODO (feature/auth): implement fully —
      1. Decode the JWT with `core.security.decode_token`
      2. Verify the token is not blacklisted (Redis, see Rules.md 1.7)
      3. Load the user from the database via `auth.repository`
      4. Return a `User` object exposing user_id, tenant_id,
         business_type and role for downstream `Depends()` chains
         (e.g. `get_tenant_id`).

    Until implemented, this stub intentionally raises so that no
    protected route can silently bypass authentication.
    """
    raise NotImplementedError("get_current_user is implemented in feature/auth")
