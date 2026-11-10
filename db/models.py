"""
SQLAlchemy declarative models.

These map onto the tables created by `kard_database.sql` (the SQL
file is the source of truth for schema/enums/constraints — models
here must stay in sync with it, not the other way around).

Each feature branch adds its own models against this shared `Base`
so `Base.metadata` stays consistent across the whole application.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String, Text
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

product_type_enum = ENUM(
    "insumo", "final",
    name="product_type_enum",
    create_type=False,
)

inventory_action_enum = ENUM(
    "sell", "restock", "adjust", "create",
    name="inventory_action_enum",
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
    slug = Column(String(60), unique=True, nullable=True)
    is_public = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    # ── Suscripción (migración 006) ───────────────────────────────────
    # Stripe es la fuente de verdad del cobro; aquí solo se guarda lo
    # mínimo para reconocer al negocio y decidir si tiene acceso sin
    # llamar a su API en cada request.
    stripe_customer_id = Column(String(60), nullable=True)
    stripe_subscription_id = Column(String(60), nullable=True)
    subscription_status = Column(String(30), nullable=False, default="none")
    plan = Column(String(20), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
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
    # Equipo del negocio (migración 008): nombre, puesto y código de empleado.
    full_name = Column(String(120), nullable=True)
    job_title = Column(String(30), nullable=True)
    employee_code = Column(String(12), nullable=True)
    # Última vez que se restableció la contraseña por correo (migración 013): las sesiones previas dejan de valer.
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    tenant = relationship("Tenant", back_populates="users")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<User id={self.id!r} email={self.email!r} role={self.role!r}>"


class Product(Base):
    """
    A catalog item belonging to exactly one tenant.

    `metadata` is JSONB whose *shape* depends on the owning tenant's
    `business_type` (restaurant vs. optician); the schema is enforced
    at the service layer (Rules.md section 8), not by the database.

    NOTE: the Python attribute is named `metadata_` (with a trailing
    underscore) because `metadata` is reserved on `Base` — it still
    maps onto the real `metadata` column via `Column("metadata", ...)`.
    """

    __tablename__ = "products"

    id = Column(String(60), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_type = Column(product_type_enum, nullable=False)
    name = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Numeric(10, 2), nullable=False, default=0)
    inventory_count = Column(Integer, nullable=False, default=0)
    low_stock_threshold = Column(Integer, nullable=False, default=5)
    # Claves para encontrar y cobrar rápido (migración 007). Opcionales y únicas
    # por negocio entre sus productos vigentes; la unicidad la garantizan los
    # índices parciales de la base, no solo el servicio.
    sku = Column(String(64), nullable=True)
    barcode = Column(String(64), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(50), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    tenant = relationship("Tenant")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Product id={self.id!r} name={self.name!r} tenant_id={self.tenant_id!r}>"


class InventoryTransaction(Base):
    """
    Immutable record of a single inventory movement (Rules.md section 11).

    Never updated or deleted after creation — `quantity_after` is
    enforced by a DB CHECK constraint to equal `quantity_before +
    quantity_change`, so this table is also a tamper-evident audit
    trail of every stock change, independent of `audit_logs`.
    """

    __tablename__ = "inventory_transactions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    product_id = Column(String(60), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, index=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    action = Column(inventory_action_enum, nullable=False)
    quantity_change = Column(Integer, nullable=False)
    quantity_before = Column(Integer, nullable=False)
    quantity_after = Column(Integer, nullable=False)
    user_id = Column(String(50), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    product = relationship("Product")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"<InventoryTransaction id={self.id} product_id={self.product_id!r} "
            f"action={self.action!r} change={self.quantity_change}>"
        )


class Image(Base):
    """
    A media asset (URL) attached to any ownable entity via the polymorphic
    `owner_type` / `owner_id` pair (Rules.md pattern — polymorphic associations).

    Today only `owner_type = 'product'` is used; tomorrow the same table can
    hold tenant logos, category banners, etc., with zero schema changes.

    Images are tenant-isolated: `tenant_id` is always set from the verified
    JWT (Rules.md section 7) and every query filters by it, so a row that
    belongs to another tenant is invisible — indistinguishable from non-existent.

    `sort_order` controls display order within a gallery; lower = earlier.
    The primary image is conventionally `sort_order = 0`.
    """

    __tablename__ = "images"

    id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    owner_type = Column(String(50), nullable=False)
    owner_id = Column(String(50), nullable=False)
    url = Column(Text, nullable=False)
    alt_text = Column(String(255), nullable=True)
    sort_order = Column(SmallInteger, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Image id={self.id!r} owner_type={self.owner_type!r} owner_id={self.owner_id!r}>"


class Consumer(Base):
    """
    A global B2C identity — one account spans all public storefronts.

    Deliberately has no `tenant_id`: a consumer is a platform-level
    entity, not scoped to any single business. The tenant is always
    resolved from the URL slug when needed.
    """

    __tablename__ = "consumers"

    id = Column(String(30), primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(100), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    # Ver User.password_changed_at (migración 013).
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Consumer id={self.id!r} email={self.email!r}>"


class PasswordReset(Base):
    """
    Un enlace enviado por correo para restablecer la contraseña (migración 013).

    Solo se guarda la huella (SHA-256) del enlace. Sirve una vez (`used_at`) y caduca (`expires_at`).
    `account_kind` dice a qué tabla apunta `account_id`: 'user' (negocio) o 'consumer' (cliente).
    """

    __tablename__ = "password_resets"

    id = Column(String(40), primary_key=True)
    account_kind = Column(String(10), nullable=False)
    account_id = Column(String(50), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    requested_ip = Column(String(64), nullable=True)


class ConsumerEvent(Base):
    """
    A behavioral event recorded when a visitor interacts with a public storefront.

    `consumer_id` is NULL for anonymous visitors — the AI engine in Fase 3 will
    correlate anonymous → authenticated sessions. Creating this table in Paso 1
    ensures behavioral data accumulates from the moment the first storefront goes live.
    """

    __tablename__ = "consumer_events"

    id = Column(String(30), primary_key=True)
    consumer_id = Column(String(30), nullable=True, index=True)
    tenant_id = Column(String(30), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<ConsumerEvent id={self.id!r} event_type={self.event_type!r} tenant_id={self.tenant_id!r}>"


class Order(Base):
    """
    Una venta de un negocio, sin importar por dónde entró.

    Dos ejes independientes describen cada venta (migración 005):

      captured_by       quién la capturó   → staff (POS) | self (el cliente)
      fulfillment_type  cómo la recibe     → dine_in | takeaway | pickup | delivery

    Y dos estados que NO son el mismo:

      status              estado de PAGO     → pending | paid | refunded | canceled
      fulfillment_status  estado de ENTREGA  → preparing | ready | in_transit | completed | canceled

    Separarlos es necesario porque son ortogonales: en una mesa se pide
    primero y se paga al final, así que un pedido puede estar servido y
    sin pagar; y un envío contra entrega viaja pagado-no.

    `consumer_id` es nullable: una venta de mostrador no tiene cuenta de
    cliente detrás. El CHECK `orders_has_an_author` garantiza que siempre
    haya un consumer o un usuario del negocio.
    """

    __tablename__ = "orders"

    id = Column(String(50), primary_key=True)
    consumer_id = Column(String(30), ForeignKey("consumers.id", ondelete="RESTRICT"), nullable=True, index=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)

    # Pago
    status = Column(String(20), nullable=False, default="pending")
    payment_method = Column(String(20), nullable=True)

    # Origen y entrega
    captured_by = Column(String(10), nullable=False, default="self")
    fulfillment_type = Column(String(20), nullable=False, default="pickup")
    fulfillment_status = Column(String(20), nullable=False, default="preparing")

    # Quién cobró (solo ventas de mostrador)
    user_id = Column(String(50), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Importes. El envío va aparte para que no infle el ingreso por productos.
    subtotal = Column(Numeric(10, 2), nullable=False)
    shipping_cost = Column(Numeric(10, 2), nullable=False, default=0)
    total = Column(Numeric(10, 2), nullable=False)

    # Caja (migración 007). La base garantiza
    #   total = subtotal - discount_amount + shipping_cost + tip_amount
    # y, en efectivo, change_given = amount_received - total.
    discount_amount = Column(Numeric(10, 2), nullable=False, default=0)
    tip_amount = Column(Numeric(10, 2), nullable=False, default=0)
    amount_received = Column(Numeric(10, 2), nullable=True)
    change_given = Column(Numeric(10, 2), nullable=True)
    cash_session_id = Column(String(50), ForeignKey("cash_sessions.id", ondelete="RESTRICT"), nullable=True, index=True)

    # Cobro idempotente (migración 010). La llave la genera el cliente por
    # ticket; la huella detecta la misma llave con otro contenido. Única por
    # negocio (índice parcial `uq_orders_tenant_idempotency_key`).
    idempotency_key = Column(String(80), nullable=True)
    request_hash = Column(String(64), nullable=True)

    # Caja desde la que se cobró (migración 012): copia del nombre de ese momento.
    register_name = Column(String(60), nullable=True)

    # Datos de la entrega. La dirección es una COPIA del momento de la compra,
    # no una referencia: si el cliente la edita después, el pedido histórico
    # debe seguir diciendo a dónde se mandó.
    shipping_address = Column(JSONB, nullable=True)
    table_number = Column(String(20), nullable=True)
    customer_name = Column(String(120), nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    items = relationship("OrderItem", back_populates="order")
    tenant = relationship("Tenant")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Order id={self.id!r} status={self.status!r} total={self.total}>"


class CashSession(Base):
    """
    Un turno de caja: del "abrir caja" al "cerrar caja".

    Solo puede haber uno abierto por negocio a la vez (índice parcial
    `uq_cash_sessions_one_open`). Al cerrarlo se congela `summary`, de modo que
    el corte histórico no cambie si una venta se reembolsa o cancela después.

    `difference = counted_cash - expected_cash`: negativo es un faltante y
    positivo un sobrante.
    """

    __tablename__ = "cash_sessions"

    id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = Column(String(10), nullable=False, default="open")

    opened_by = Column(String(50), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    opened_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    opening_float = Column(Numeric(10, 2), nullable=False, default=0)

    closed_by = Column(String(50), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    expected_cash = Column(Numeric(10, 2), nullable=True)
    counted_cash = Column(Numeric(10, 2), nullable=True)
    difference = Column(Numeric(10, 2), nullable=True)
    summary = Column(JSONB, nullable=True)
    notes = Column(String(500), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<CashSession id={self.id!r} status={self.status!r} tenant={self.tenant_id!r}>"


class OrderItem(Base):
    """A single line in an order — product + quantity + price locked at purchase time."""

    __tablename__ = "order_items"

    id = Column(String(50), primary_key=True)
    order_id = Column(String(50), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    product_id = Column(String(60), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    product_name = Column(String(500), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OrderItem order_id={self.order_id!r} product_id={self.product_id!r} qty={self.quantity}>"


class ProductRecipe(Base):
    """
    Recipe / Bill-of-Materials line: a final product (e.g. a dish) is made
    of one or more insumos (ingredients), each consumed in a fixed quantity
    per unit sold.

    When a final product with a recipe is sold, its own stock is decremented
    AND every linked insumo's stock is decremented by `quantity * units_sold`,
    all inside the same atomic transaction as the order.

    `quantity` is an integer number of insumo stock-units consumed per unit
    of the final product (kept integer to stay consistent with the integer
    inventory_count + inventory_transactions audit trail).
    """

    __tablename__ = "product_recipes"

    id = Column(String(50), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    final_product_id = Column(String(60), ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    insumo_product_id = Column(String(60), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ProductRecipe final={self.final_product_id!r} "
            f"insumo={self.insumo_product_id!r} qty={self.quantity}>"
        )


class StripeEvent(Base):
    """
    Evento de Stripe ya aplicado.

    Stripe reintenta un webhook hasta recibir un 2xx y puede entregar el
    mismo evento más de una vez. Registrar su id antes de actuar vuelve
    idempotente el procesamiento: el reintento se reconoce y se ignora en
    lugar de volver a aplicar el cambio.
    """

    __tablename__ = "stripe_events"

    id = Column(String(80), primary_key=True)          # evt_... tal cual lo manda Stripe
    type = Column(String(80), nullable=False)
    processed_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StripeEvent id={self.id!r} type={self.type!r}>"


class Device(Base):
    """
    Una computadora que la gerencia autorizó para el día (una caja registradora).

    El token del equipo vive solo en su navegador; aquí se guarda su hash, así
    que una filtración de la base no entrega equipos autorizados.
    """

    __tablename__ = "devices"

    id = Column(String(40), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    name = Column(String(60), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False)
    authorized_by = Column(String(50), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_agent = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    # Quién opera la caja ahora (migración 011). Su celular comparte el ticket de la caja.
    operator_id = Column(String(50), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    operator_since = Column(DateTime(timezone=True), nullable=True)


class TicketDraft(Base):
    """
    Copia en la base del ticket que se arma en una caja (migración 011).

    El ticket vivo está en Redis; esto permite recuperarlo si Redis se reinicia y
    es el almacén completo cuando no hay Redis. `payload` guarda líneas y llaves
    de operaciones recientes; `uid` y `rev` van aparte para consultarlos sin abrirlo.
    """

    __tablename__ = "ticket_drafts"

    device_id = Column(String(40), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uid = Column(String(40), nullable=False)
    rev = Column(Integer, nullable=False, default=0)
    payload = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class AuthChallenge(Base):
    """Un QR en pantalla esperando que un celular lo apruebe (autorizar un equipo o abrir sesión)."""

    __tablename__ = "auth_challenges"

    id = Column(String(40), primary_key=True)
    kind = Column(String(10), nullable=False)
    tenant_id = Column(String(50), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    device_id = Column(String(40), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True)
    secret_hash = Column(String(64), nullable=False)
    verification_code = Column(String(4), nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    user_agent = Column(String(300), nullable=True)
    ip_address = Column(String(64), nullable=True)
    status = Column(String(10), nullable=False, default="pending")
    approved_by = Column(String(50), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    device_name = Column(String(60), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
