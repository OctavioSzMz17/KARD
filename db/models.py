"""
SQLAlchemy declarative models.

These map onto the tables created by `kard_database.sql` (the SQL
file is the source of truth for schema/enums/constraints — models
here must stay in sync with it, not the other way around).

Each feature branch adds its own models against this shared `Base`
so `Base.metadata` stays consistent across the whole application.
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ---------------------------------------------------------------------------
# Enum columns: map onto the PostgreSQL enum *types* already created by
# kard_database.sql. `create_type=False` tells SQLAlchemy not to try to
# CREATE TYPE — the raw SQL migration owns enum lifecycle, not the ORM.
# ---------------------------------------------------------------------------
business_type_enum = ENUM(
    "restaurant", "optician", "platform",
    name="business_type_enum",
    create_type=False,
)

user_role_enum = ENUM(
    "saas_admin", "business_admin", "staff",
    name="user_role_enum",
    create_type=False,
)


class Tenant(Base):
    """
    A registered business (restaurant or optician).

    Root of multi-tenant isolation (Rules.md section 7): every other
    tenant-scoped table carries a `tenant_id` referencing this table.
    """

    __tablename__ = "tenants"

    id = Column(String(50), primary_key=True)
    business_name = Column(String(255), nullable=False)
    business_type = Column(business_type_enum, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    phone = Column(String(20), nullable=True)
    config = Column(JSONB, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    users = relationship("User", back_populates="tenant")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Tenant id={self.id!r} business_name={self.business_name!r}>"


class User(Base):
    """An admin/staff user. Always scoped to exactly one tenant."""

    __tablename__ = "users"

    id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(user_role_enum, nullable=False, default="business_admin")
    is_active = Column(Boolean, nullable=False, default=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    tenant = relationship("Tenant", back_populates="users")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<User id={self.id!r} email={self.email!r} role={self.role!r}>"
