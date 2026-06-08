"""
Shared SQLAlchemy declarative base.

No ORM models are defined here yet — each feature branch
(feature/auth, feature/products, feature/businessadmin,
feature/saasadmin, ...) defines its own models against this
shared `Base` so that `Base.metadata` stays consistent across
the whole application.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
