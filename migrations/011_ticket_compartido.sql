-- ============================================================================
-- 011 · Ticket compartido entre la caja y el celular
--
-- Cuando una persona entra a una caja registradora, su celular y la computadora
-- de esa caja deben ver el MISMO ticket: lo que escanea el celular aparece en la
-- computadora, como si fuera un escáner de mano, y lo que se borra en una se
-- borra en la otra.
--
--   · devices.operator_id / operator_since
--       Quién está operando cada caja ahora. Es lo que permite al celular de esa
--       persona encontrar SU caja sin pedir ningún QR extra: la persona ya entró
--       a ella (por el QR de desbloqueo o con sus datos en la computadora).
--
--   · ticket_drafts
--       Copia en la base del ticket que se está armando en cada caja. El ticket
--       vivo está en Redis (con caducidad); esta copia permite recuperarlo si
--       Redis se reinicia, y es el almacén completo cuando no hay Redis.
--
-- Migración ADITIVA: lo anterior sigue funcionando sin tocar nada.
--
-- Ejecutar en el SQL Editor de Neon. Es idempotente.
-- ============================================================================

BEGIN;

ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS operator_id    VARCHAR(50) REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS operator_since TIMESTAMPTZ;

-- Buscar "¿qué caja opera esta persona?" en cada consulta del celular.
CREATE INDEX IF NOT EXISTS ix_devices_operator
    ON devices (tenant_id, operator_id)
    WHERE operator_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ticket_drafts (
    device_id   VARCHAR(40)  PRIMARY KEY REFERENCES devices(id) ON DELETE CASCADE,
    tenant_id   VARCHAR(50)  NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    -- Identifica ESTE ticket; cambia al cobrarlo o vaciarlo. El cobro lo usa como
    -- llave de idempotencia: dos pantallas que cobran el mismo ticket no lo duplican.
    uid         VARCHAR(40)  NOT NULL,
    -- Sube con cada cambio; las pantallas lo comparan para saber si hay algo nuevo.
    rev         INTEGER      NOT NULL DEFAULT 0,
    payload     JSONB        NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ  NOT NULL,

    CONSTRAINT ticket_drafts_rev_not_negative CHECK (rev >= 0)
);

CREATE INDEX IF NOT EXISTS ix_ticket_drafts_tenant  ON ticket_drafts (tenant_id);
CREATE INDEX IF NOT EXISTS ix_ticket_drafts_expires ON ticket_drafts (expires_at);

COMMENT ON COLUMN devices.operator_id    IS 'Persona que opera esta caja ahora (entró por QR o con sus datos); su celular comparte el ticket de la caja';
COMMENT ON TABLE  ticket_drafts          IS 'Copia del ticket en armado de cada caja; el ticket vivo está en Redis';
COMMENT ON COLUMN ticket_drafts.uid      IS 'Identificador del ticket; se usa como Idempotency-Key del cobro';

COMMIT;
