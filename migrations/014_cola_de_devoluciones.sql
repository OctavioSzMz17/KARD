-- ============================================================================
-- 014 · Cola de devoluciones de inventario
--
-- Cancelar un pedido devuelve al inventario lo que se descontó al venderlo.
-- Hacerlo dentro de la petición obligaba a bloquear los insumos de ESE pedido
-- mientras se cancelaba: dos cancelaciones que comparten un insumo (el maíz, el
-- pollo) se esperaban entre sí, y si tomaban los insumos en orden distinto la
-- base las trababa y tumbaba una (deadlock).
--
-- Ahora la cancelación solo marca el pedido y deja UN trabajo en esta tabla;
-- un proceso aparte aplica las devoluciones una por una, tomando los insumos
-- siempre en el mismo orden. Cancelar es instantáneo y las cancelaciones no se
-- estorban.
--
--   · order_id  ÚNICO: es la llave de idempotencia. Un pedido solo puede tener
--               una devolución, pase lo que pase (doble clic, reintento,
--               dos servidores a la vez): la base no deja guardar una segunda.
--   · lines     lo que se devuelve, fijado al cancelar: [{product_id, quantity,
--               note}]. Reintentar un trabajo no recalcula nada.
--   · status    pending → done. Pasa a done en la MISMA transacción que suma el
--               inventario: o ocurre todo o nada, así que nunca se devuelve a
--               medias ni dos veces. failed = agotó sus reintentos y necesita
--               revisión manual.
--
-- Migración ADITIVA: una tabla nueva, no toca nada existente. Ejecutar ANTES de
-- desplegar el back que la usa. Es idempotente.
--
-- Ejecutar en el SQL Editor de Neon.
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS stock_returns (
    id               VARCHAR(40)  PRIMARY KEY,
    tenant_id        VARCHAR(50)  NOT NULL REFERENCES tenants (id)  ON DELETE RESTRICT,
    order_id         VARCHAR(50)  NOT NULL REFERENCES orders (id)   ON DELETE RESTRICT,
    lines            JSONB        NOT NULL DEFAULT '[]'::jsonb,
    status           VARCHAR(10)  NOT NULL DEFAULT 'pending',
    attempts         INTEGER      NOT NULL DEFAULT 0,
    last_error       TEXT,
    user_id          VARCHAR(50)  REFERENCES users (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    next_attempt_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    processed_at     TIMESTAMPTZ,

    CONSTRAINT stock_returns_status_valid CHECK (status IN ('pending', 'done', 'failed')),
    CONSTRAINT stock_returns_attempts_valid CHECK (attempts >= 0)
);

-- La red de seguridad final de la idempotencia: una devolución por pedido.
CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_returns_order ON stock_returns (order_id);

-- Lo único que consulta el proceso: lo pendiente que ya toca intentar. Parcial,
-- así que no crece con los trabajos ya terminados.
CREATE INDEX IF NOT EXISTS ix_stock_returns_pending
    ON stock_returns (next_attempt_at)
    WHERE status = 'pending';

COMMENT ON TABLE  stock_returns          IS 'Cola de devoluciones de inventario por pedidos cancelados; una por pedido';
COMMENT ON COLUMN stock_returns.order_id IS 'Llave de idempotencia: único, un pedido no puede devolver dos veces';
COMMENT ON COLUMN stock_returns.lines    IS 'Qué se devuelve, fijado al cancelar: [{product_id, quantity, note}]';
COMMENT ON COLUMN stock_returns.status   IS 'pending (por aplicar) | done (aplicada) | failed (agotó reintentos; revisar a mano)';

COMMIT;
