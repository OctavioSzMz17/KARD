"""General-purpose helper functions shared across feature modules."""

import uuid


def generate_id(prefix: str, length: int = 12) -> str:
    """
    Generate a short, prefixed unique identifier, e.g. `t_3f9a2b1c9d4e`.

    Matches the ID conventions documented in kard_database.sql:
      - tenants.id   -> 't_{uuid_short}'
      - users.id     -> 'u_{uuid_short}'
      - products.id  -> 'p_{6chars}{business_type}_{tenant_id}'
    """
    return f"{prefix}_{uuid.uuid4().hex[:length]}"
