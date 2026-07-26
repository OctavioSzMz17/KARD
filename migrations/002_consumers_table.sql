-- =============================================================
-- Migration 002 — PASO 5: Tabla consumers
-- Identidad global B2C — sin tenant_id (es de plataforma, no de negocio)
-- =============================================================

CREATE TABLE consumers (
    id              VARCHAR(30)   PRIMARY KEY,
    email           VARCHAR(255)  UNIQUE NOT NULL,
    hashed_password VARCHAR(255)  NOT NULL,
    name            VARCHAR(100),
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  consumers                  IS 'Global B2C consumer identities. No tenant_id — one account works across all storefronts.';
COMMENT ON COLUMN consumers.id               IS 'Consumer identifier: con_{uuid_short}';
COMMENT ON COLUMN consumers.hashed_password  IS 'bcrypt hash, min 12 rounds';

CREATE INDEX idx_consumers_email ON consumers (email);
