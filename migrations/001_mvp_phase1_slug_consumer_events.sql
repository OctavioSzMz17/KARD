-- =============================================================
-- Migration 001 — MVP Fase 1 B2B2C
-- Adds: tenants.slug, tenants.is_public, table consumer_events
-- =============================================================

-- Step 1: Add slug + is_public to tenants
ALTER TABLE tenants
  ADD COLUMN slug      VARCHAR(60) UNIQUE,
  ADD COLUMN is_public BOOLEAN     NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN tenants.slug      IS 'URL-friendly identifier for public storefront: papeleria-don-jorge';
COMMENT ON COLUMN tenants.is_public IS 'Set to TRUE by business_admin to expose the public storefront';

-- Partial unique index on slug (NULL slugs are excluded — pre-onboarding tenants have no slug yet)
CREATE UNIQUE INDEX uq_tenant_slug ON tenants (slug) WHERE slug IS NOT NULL;

-- Step 2: Create consumer_events table
-- Filled from Paso 3 (storefront), processed in Fase 3 (AI engine).
-- Creating now so behavioral data accumulates from day one.
CREATE TABLE consumer_events (
    id           VARCHAR(30)  PRIMARY KEY,
    consumer_id  VARCHAR(30),                                          -- NULL for anonymous visitors
    tenant_id    VARCHAR(30)  NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    event_type   VARCHAR(50)  NOT NULL,                               -- 'view_product', 'search', 'view_catalog'
    payload      JSONB,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  consumer_events              IS 'Behavioral events from the B2C storefront. Processed by AI engine in Fase 3.';
COMMENT ON COLUMN consumer_events.consumer_id  IS 'NULL = anonymous visitor; set once the consumer authenticates';
COMMENT ON COLUMN consumer_events.event_type   IS 'view_catalog | view_product | search | add_to_cart';
COMMENT ON COLUMN consumer_events.payload      IS 'Event-specific data: product_id, search_term, etc.';

CREATE INDEX idx_consumer_events_consumer ON consumer_events (consumer_id)              WHERE consumer_id IS NOT NULL;
CREATE INDEX idx_consumer_events_tenant   ON consumer_events (tenant_id, created_at DESC);
