-- =============================================================
-- KARD · Database Schema
-- PostgreSQL (Neon compatible)
-- =============================================================

-- ===================== EXTENSIONS =====================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ===================== ENUMS =====================
CREATE TYPE business_type_enum AS ENUM ('restaurant', 'optician');
CREATE TYPE user_role_enum     AS ENUM ('saas_admin', 'business_admin', 'staff');
CREATE TYPE product_type_enum  AS ENUM ('insumo', 'final');
CREATE TYPE inventory_action_enum AS ENUM ('sell', 'restock', 'adjust', 'create');

-- =============================================================
-- TABLE: tenants
-- One record per registered business (restaurant or optician)
-- =============================================================
CREATE TABLE tenants (
    id            VARCHAR(50)        PRIMARY KEY,
    business_name VARCHAR(255)       NOT NULL,
    business_type business_type_enum NOT NULL,
    email         VARCHAR(255)       UNIQUE NOT NULL,
    phone         VARCHAR(20),
    config        JSONB              NOT NULL DEFAULT '{}',
    -- config stores: primary_color, secondary_color, accent_color,
    --                logo_url, business_display_name, widget_settings
    is_active     BOOLEAN            NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ        NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ        NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  tenants              IS 'Registered businesses on the KARD SaaS platform';
COMMENT ON COLUMN tenants.id           IS 'Tenant identifier: t_{uuid_short}';
COMMENT ON COLUMN tenants.config       IS 'JSONB: theme colors, logo, widget visibility preferences';
COMMENT ON COLUMN tenants.business_type IS 'Determines which metadata schema products must follow';

-- =============================================================
-- TABLE: users
-- Admin and staff users, always scoped to a tenant
-- =============================================================
CREATE TABLE users (
    id            VARCHAR(50)     PRIMARY KEY,
    tenant_id     VARCHAR(50)     NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    email         VARCHAR(255)    UNIQUE NOT NULL,
    password_hash VARCHAR(255)    NOT NULL,
    role          user_role_enum  NOT NULL DEFAULT 'business_admin',
    is_active     BOOLEAN         NOT NULL DEFAULT TRUE,
    last_login    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  users           IS 'Platform users scoped to a tenant';
COMMENT ON COLUMN users.id        IS 'User identifier: u_{uuid_short}';
COMMENT ON COLUMN users.role      IS 'saas_admin = platform owner, business_admin = tenant owner, staff = limited access';
COMMENT ON COLUMN users.password_hash IS 'bcrypt hash, min 12 rounds';

-- =============================================================
-- TABLE: products
-- Core product catalog, shared schema + JSONB for business-specific fields
-- =============================================================
CREATE TABLE products (
    id                  VARCHAR(60)       PRIMARY KEY,
    tenant_id           VARCHAR(50)       NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    product_type        product_type_enum NOT NULL,
    name                VARCHAR(500)      NOT NULL,
    description         TEXT,
    price               NUMERIC(10,2)     NOT NULL DEFAULT 0.00
                        CONSTRAINT products_price_positive CHECK (price >= 0),
    inventory_count     INT               NOT NULL DEFAULT 0
                        CONSTRAINT products_inventory_non_negative CHECK (inventory_count >= 0),
    low_stock_threshold INT               NOT NULL DEFAULT 5
                        CONSTRAINT products_threshold_positive CHECK (low_stock_threshold >= 0),
    metadata            JSONB             NOT NULL DEFAULT '{}',
    -- Restaurant example:  {"unidad_medida":"gramos","perecedero":true,"dias_expiracion":3}
    -- Optician example:    {"esfera":"-2.00","cilindro":"-0.50","eje":180}
    is_deleted          BOOLEAN           NOT NULL DEFAULT FALSE,
    created_by          VARCHAR(50)       REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

    -- Prevent duplicate product names within the same tenant
    CONSTRAINT uq_product_name_tenant UNIQUE (tenant_id, name)
);

COMMENT ON TABLE  products                   IS 'Product catalog, one table for all tenants';
COMMENT ON COLUMN products.id                IS 'Product ID: p_{6chars}{business_type}_{tenant_id}';
COMMENT ON COLUMN products.metadata          IS 'JSONB: business-specific fields. Schema enforced at service layer';
COMMENT ON COLUMN products.is_deleted        IS 'Soft delete flag. Hard deletes are never performed';
COMMENT ON COLUMN products.low_stock_threshold IS 'Alert fires when inventory_count falls below this value';

-- =============================================================
-- TABLE: inventory_transactions
-- Immutable audit trail of every stock change
-- =============================================================
CREATE TABLE inventory_transactions (
    id              BIGSERIAL     PRIMARY KEY,
    product_id      VARCHAR(60)   NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    tenant_id       VARCHAR(50)   NOT NULL REFERENCES tenants(id)  ON DELETE RESTRICT,
    action          inventory_action_enum NOT NULL,
    quantity_change INT           NOT NULL,
    quantity_before INT           NOT NULL,
    quantity_after  INT           NOT NULL,
    user_id         VARCHAR(50)   REFERENCES users(id) ON DELETE SET NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_quantity_after CHECK (quantity_after = quantity_before + quantity_change)
);

COMMENT ON TABLE  inventory_transactions                IS 'Immutable log of every inventory movement';
COMMENT ON COLUMN inventory_transactions.quantity_change IS 'Negative for sell/adjust-down, positive for restock';
COMMENT ON COLUMN inventory_transactions.quantity_after  IS 'Enforced by CHECK: must equal before + change';

-- =============================================================
-- TABLE: audit_logs
-- General audit trail for all CRUD operations
-- =============================================================
CREATE TABLE audit_logs (
    id            BIGSERIAL    PRIMARY KEY,
    tenant_id     VARCHAR(50)  NOT NULL,
    user_id       VARCHAR(50),
    action        VARCHAR(50)  NOT NULL,
    -- Examples: CREATE_PRODUCT, UPDATE_PRODUCT, DELETE_PRODUCT,
    --           LOGIN, LOGOUT, UPDATE_THEME, REGISTER_TENANT
    resource_type VARCHAR(50)  NOT NULL,
    resource_id   VARCHAR(60),
    before_data   JSONB,
    after_data    JSONB,
    ip_address    VARCHAR(45),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  audit_logs         IS 'General audit trail for all platform operations';
COMMENT ON COLUMN audit_logs.action  IS 'Verb in SCREAMING_SNAKE_CASE: CREATE_PRODUCT, LOGIN, etc.';
COMMENT ON COLUMN audit_logs.before_data IS 'State before the operation (null for CREATE)';
COMMENT ON COLUMN audit_logs.after_data  IS 'State after the operation (null for DELETE)';

-- =============================================================
-- TABLE: customers
-- Per-tenant customer directory (prep for Stage 3 public catalog)
-- =============================================================
CREATE TABLE customers (
    id          VARCHAR(50)  PRIMARY KEY,
    tenant_id   VARCHAR(50)  NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    full_name   VARCHAR(255) NOT NULL,
    email       VARCHAR(255),
    phone       VARCHAR(20),
    is_deleted  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_by  VARCHAR(50)  REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Prevent duplicate customer emails within the same tenant
    CONSTRAINT uq_customer_email_tenant UNIQUE (tenant_id, email)
);

COMMENT ON TABLE  customers           IS 'Per-tenant customer directory';
COMMENT ON COLUMN customers.id        IS 'Customer identifier: c_{uuid_short}';
COMMENT ON COLUMN customers.is_deleted IS 'Soft delete flag. Hard deletes are never performed';

-- =============================================================
-- TABLE: images (polymorphic media assets)
-- Stores image URLs for any ownable entity (products today,
-- tenants/categories/etc. tomorrow) via the owner_type + owner_id pair.
-- Option A (duplicate-row) polymorphic association: each owner gets its
-- own row even when two owners share the same URL, so deletions are
-- always isolated. Tenant-scoped: tenant_id is always set from the
-- verified JWT and every query filters by it.
-- =============================================================
CREATE TABLE images (
    id          VARCHAR(50)  PRIMARY KEY,
    tenant_id   VARCHAR(50)  NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    owner_type  VARCHAR(50)  NOT NULL,
    owner_id    VARCHAR(50)  NOT NULL,
    url         TEXT         NOT NULL,
    alt_text    VARCHAR(255),
    sort_order  SMALLINT     NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  images             IS 'Polymorphic media asset registry (URL-based)';
COMMENT ON COLUMN images.id          IS 'Image identifier: img_{uuid_short}';
COMMENT ON COLUMN images.owner_type  IS 'Entity type that owns this image, e.g. ''product''';
COMMENT ON COLUMN images.owner_id    IS 'PK of the owning entity (must match owner_type table)';
COMMENT ON COLUMN images.sort_order  IS 'Display order within a gallery; 0 = primary/main image';

-- =============================================================
-- INDEXES
-- =============================================================

-- tenants
CREATE INDEX idx_tenants_business_type ON tenants(business_type);
CREATE INDEX idx_tenants_active        ON tenants(is_active) WHERE is_active = TRUE;

-- users
CREATE INDEX idx_users_tenant          ON users(tenant_id);
CREATE INDEX idx_users_email           ON users(email);
CREATE INDEX idx_users_tenant_active   ON users(tenant_id, is_active) WHERE is_active = TRUE;

-- products: most frequent filter is tenant_id + not deleted
CREATE INDEX idx_products_tenant
    ON products(tenant_id)
    WHERE is_deleted = FALSE;

CREATE INDEX idx_products_tenant_type
    ON products(tenant_id, product_type)
    WHERE is_deleted = FALSE;

-- products: JSONB full-text search (search by metadata fields)
CREATE INDEX idx_products_metadata
    ON products USING GIN(metadata);

-- products: dashboard low-stock widget
CREATE INDEX idx_products_low_stock
    ON products(tenant_id, inventory_count)
    WHERE is_deleted = FALSE;

-- products: out-of-stock filter
CREATE INDEX idx_products_out_of_stock
    ON products(tenant_id)
    WHERE inventory_count = 0 AND is_deleted = FALSE;

-- inventory_transactions: history per product
CREATE INDEX idx_inv_tx_product
    ON inventory_transactions(product_id, created_at DESC);

-- inventory_transactions: dashboard last-N-days summary
CREATE INDEX idx_inv_tx_tenant_date
    ON inventory_transactions(tenant_id, created_at DESC);

-- audit_logs: query by tenant + date
CREATE INDEX idx_audit_tenant_date
    ON audit_logs(tenant_id, created_at DESC);

-- audit_logs: query by resource
CREATE INDEX idx_audit_resource
    ON audit_logs(resource_type, resource_id);

-- customers
CREATE INDEX idx_customers_tenant
    ON customers(tenant_id)
    WHERE is_deleted = FALSE;

-- =============================================================
-- AUTO-UPDATE updated_at TRIGGER
-- =============================================================
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_updated_at_tenants
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_users
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_products
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_customers
    BEFORE UPDATE ON customers
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- =============================================================
-- VIEWS
-- =============================================================

-- Active products enriched with tenant business_type
CREATE VIEW v_active_products AS
SELECT
    p.id,
    p.tenant_id,
    p.product_type,
    p.name,
    p.description,
    p.price,
    p.inventory_count,
    p.low_stock_threshold,
    p.metadata,
    p.created_by,
    p.created_at,
    p.updated_at,
    t.business_type,
    t.business_name
FROM products p
JOIN tenants t ON p.tenant_id = t.id
WHERE p.is_deleted = FALSE;

COMMENT ON VIEW v_active_products IS 'Active products joined with tenant info. Most queries should use this view.';

-- Low stock products (inventory below threshold, not zero)
CREATE VIEW v_low_stock AS
SELECT *
FROM v_active_products
WHERE inventory_count > 0
  AND inventory_count < low_stock_threshold
ORDER BY inventory_count ASC;

COMMENT ON VIEW v_low_stock IS 'Products approaching zero stock. Used for dashboard alert widget.';

-- Out of stock products
CREATE VIEW v_out_of_stock AS
SELECT *
FROM v_active_products
WHERE inventory_count = 0
ORDER BY updated_at DESC;

COMMENT ON VIEW v_out_of_stock IS 'Products with zero inventory.';

-- Dashboard summary per tenant (used by dashboard overview widget)
CREATE VIEW v_dashboard_summary AS
SELECT
    tenant_id,
    COUNT(*)                                                              AS total_products,
    COALESCE(SUM(inventory_count), 0)                                     AS total_inventory,
    COUNT(*) FILTER (WHERE inventory_count = 0)                           AS out_of_stock,
    COUNT(*) FILTER (WHERE inventory_count > 0
                       AND inventory_count < low_stock_threshold)         AS low_stock,
    COUNT(*) FILTER (WHERE product_type = 'insumo')                       AS total_insumos,
    COUNT(*) FILTER (WHERE product_type = 'final')                        AS total_finals
FROM products
WHERE is_deleted = FALSE
GROUP BY tenant_id;

COMMENT ON VIEW v_dashboard_summary IS 'Aggregated product stats per tenant. One row per tenant.';

-- Recent inventory activity per tenant (last 30 days)
CREATE VIEW v_recent_transactions AS
SELECT
    it.id,
    it.tenant_id,
    it.product_id,
    p.name        AS product_name,
    it.action,
    it.quantity_change,
    it.quantity_before,
    it.quantity_after,
    it.user_id,
    it.notes,
    it.created_at
FROM inventory_transactions it
JOIN products p ON it.product_id = p.id
WHERE it.created_at >= NOW() - INTERVAL '30 days'
ORDER BY it.created_at DESC;

COMMENT ON VIEW v_recent_transactions IS 'Inventory movements from the last 30 days, joined with product name.';

-- =============================================================
-- SEED DATA (example tenants for testing)
-- =============================================================

INSERT INTO tenants (id, business_name, business_type, email, phone, config) VALUES
(
    't_rest01',
    'Restaurante El Sabor',
    'restaurant',
    'admin@elsabor.com',
    '+52 55 1234 5678',
    '{"primary_color":"#e63946","secondary_color":"#f1faee","accent_color":"#a8dadc","logo_url":null}'
),
(
    't_opti01',
    'Óptica Visión Clara',
    'optician',
    'admin@visionclar.com',
    '+52 55 8765 4321',
    '{"primary_color":"#023e8a","secondary_color":"#e0fbfc","accent_color":"#48cae4","logo_url":null}'
);

INSERT INTO users (id, tenant_id, email, password_hash, role) VALUES
(
    'u_rest01',
    't_rest01',
    'admin@elsabor.com',
    '$2b$12$placeholder_hash_restaurant',
    'business_admin'
),
(
    'u_opti01',
    't_opti01',
    'admin@visionclar.com',
    '$2b$12$placeholder_hash_optician',
    'business_admin'
);

INSERT INTO products (id, tenant_id, product_type, name, price, inventory_count, low_stock_threshold, metadata, created_by) VALUES
(
    'p_001rest_t_rest01',
    't_rest01',
    'insumo',
    'Carne Molida Res',
    0.00,
    500,
    50,
    '{"unidad_medida":"gramos","perecedero":true,"dias_expiracion":3}',
    'u_rest01'
),
(
    'p_002rest_t_rest01',
    't_rest01',
    'final',
    'Hamburguesa Clásica',
    120.00,
    999,
    10,
    '{"unidad_medida":"pieza","perecedero":false,"categoria":"hamburguesas"}',
    'u_rest01'
),
(
    'p_001opti_t_opti01',
    't_opti01',
    'final',
    'Lente de Contacto Diario',
    500.00,
    30,
    10,
    '{"esfera":"-2.00","cilindro":"-0.50","eje":180,"material":"hidrogel"}',
    'u_opti01'
),
(
    'p_002opti_t_opti01',
    't_opti01',
    'final',
    'Armazón Titanio',
    1800.00,
    8,
    5,
    '{"material":"titanio","color":"negro","talla":"M","tipo":"completo"}',
    'u_opti01'
);

-- =============================================================
-- EXAMPLE QUERIES (commented for reference)
-- =============================================================

-- Get all active products for a tenant:
-- SELECT * FROM v_active_products WHERE tenant_id = 't_rest01';

-- Get dashboard summary for a tenant:
-- SELECT * FROM v_dashboard_summary WHERE tenant_id = 't_rest01';

-- Get low stock products for a tenant:
-- SELECT * FROM v_low_stock WHERE tenant_id = 't_opti01';

-- Search by JSONB field (restaurant: perishable items):
-- SELECT * FROM v_active_products
-- WHERE tenant_id = 't_rest01'
--   AND metadata->>'perecedero' = 'true';

-- Search by JSONB field (optician: by sphere):
-- SELECT * FROM v_active_products
-- WHERE tenant_id = 't_opti01'
--   AND metadata->>'esfera' = '-2.00';

-- Get recent transactions for a tenant:
-- SELECT * FROM v_recent_transactions WHERE tenant_id = 't_rest01';

-- Sell a product (decrease stock):
-- UPDATE products
-- SET inventory_count = inventory_count - 1
-- WHERE id = 'p_001opti_t_opti01' AND tenant_id = 't_opti01';

CREATE INDEX idx_images_owner ON images (tenant_id, owner_type, owner_id, sort_order);

-- =============================================================
-- END OF SCHEMA
-- =============================================================
