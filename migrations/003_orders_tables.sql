-- =============================================================
-- Migration 003 — orders + order_items
-- Run once on Neon after migration 002.
-- =============================================================

CREATE TABLE orders (
    id          VARCHAR(50)     PRIMARY KEY,
    consumer_id VARCHAR(30)     NOT NULL REFERENCES consumers(id) ON DELETE RESTRICT,
    tenant_id   VARCHAR(50)     NOT NULL REFERENCES tenants(id)   ON DELETE RESTRICT,
    status      VARCHAR(20)     NOT NULL DEFAULT 'pending'
                                CONSTRAINT chk_order_status
                                CHECK (status IN ('pending','paid','fulfilled','canceled')),
    total       NUMERIC(10,2)   NOT NULL CHECK (total >= 0),
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE TABLE order_items (
    id           VARCHAR(50)    PRIMARY KEY,
    order_id     VARCHAR(50)    NOT NULL REFERENCES orders(id)   ON DELETE RESTRICT,
    product_id   VARCHAR(60)    NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    product_name VARCHAR(500)   NOT NULL,
    quantity     INTEGER        NOT NULL CHECK (quantity > 0),
    unit_price   NUMERIC(10,2)  NOT NULL CHECK (unit_price >= 0),
    subtotal     NUMERIC(10,2)  NOT NULL CHECK (subtotal >= 0)
);

CREATE INDEX idx_orders_consumer    ON orders(consumer_id);
CREATE INDEX idx_orders_tenant      ON orders(tenant_id);
CREATE INDEX idx_orders_status      ON orders(status);
CREATE INDEX idx_order_items_order  ON order_items(order_id);

CREATE TRIGGER set_updated_at_orders
    BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

COMMENT ON TABLE  orders              IS 'Consumer purchase orders — one per storefront visit';
COMMENT ON COLUMN orders.status       IS 'pending | paid | fulfilled | canceled';
COMMENT ON COLUMN order_items.unit_price IS 'Price locked at time of purchase — immutable';
