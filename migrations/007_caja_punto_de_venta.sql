-- ============================================================================
-- 007 · Caja y punto de venta
--
-- Lo que hace falta para que el plan Starter sea de verdad un punto de venta:
--
--   · Turnos de caja (apertura, cierre y diferencia entre lo esperado y lo
--     contado): tabla `cash_sessions`.
--   · Descuento y propina en la venta, y cuánto pagó el cliente en efectivo
--     con su cambio: columnas nuevas en `orders`.
--   · SKU y código de barras en los productos, para buscar y cobrar rápido.
--
-- Migración ADITIVA: todo lo nuevo es opcional o tiene valor por defecto, así
-- que el código anterior (el que corra en producción mientras se despliega
-- esta versión) sigue funcionando con las filas existentes.
--
-- Ejecutar en el SQL Editor de Neon. Es idempotente.
-- ============================================================================

BEGIN;

-- ── Turnos de caja ──────────────────────────────────────────────────────────
-- Un turno es el periodo entre "abrir caja" y "cerrar caja". Al cerrar se
-- congela un resumen (`summary`) para que el corte histórico no cambie si una
-- venta se reembolsa o cancela después.
CREATE TABLE IF NOT EXISTS cash_sessions (
    id             VARCHAR(50)   PRIMARY KEY,
    tenant_id      VARCHAR(50)   NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    status         VARCHAR(10)   NOT NULL DEFAULT 'open',

    opened_by      VARCHAR(50)   REFERENCES users(id) ON DELETE SET NULL,
    opened_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    -- El "fondo": efectivo con el que arranca el cajón (para dar cambio).
    opening_float  NUMERIC(10,2) NOT NULL DEFAULT 0,

    closed_by      VARCHAR(50)   REFERENCES users(id) ON DELETE SET NULL,
    closed_at      TIMESTAMPTZ,
    -- Lo que DEBERÍA haber en el cajón, lo que realmente se contó y la resta.
    -- Negativa = faltante; positiva = sobrante.
    expected_cash  NUMERIC(10,2),
    counted_cash   NUMERIC(10,2),
    difference     NUMERIC(10,2),
    summary        JSONB,
    notes          VARCHAR(500),

    CONSTRAINT cash_sessions_status_valid
        CHECK (status IN ('open', 'closed')),
    CONSTRAINT cash_sessions_float_not_negative
        CHECK (opening_float >= 0),
    CONSTRAINT cash_sessions_count_not_negative
        CHECK (counted_cash IS NULL OR counted_cash >= 0),
    -- Un turno cerrado siempre trae su conteo completo: sin esto, un cierre
    -- a medias quedaría en la base sin que nadie lo notara.
    CONSTRAINT cash_sessions_closed_is_complete
        CHECK (status = 'open'
               OR (closed_at IS NOT NULL AND expected_cash IS NOT NULL
                   AND counted_cash IS NOT NULL AND difference IS NOT NULL))
);

-- Una sola caja abierta por negocio a la vez. Lo garantiza la base de datos,
-- no solo el código: dos cajeros abriendo "al mismo tiempo" no pueden crear
-- dos turnos.
CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_sessions_one_open
    ON cash_sessions (tenant_id) WHERE status = 'open';

CREATE INDEX IF NOT EXISTS ix_cash_sessions_tenant_opened
    ON cash_sessions (tenant_id, opened_at DESC);

-- ── Ventas: descuento, propina, efectivo recibido y turno ───────────────────
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tip_amount      NUMERIC(10,2) NOT NULL DEFAULT 0,
    -- Solo pagos en efectivo: cuánto entregó el cliente y cuánto se devolvió.
    ADD COLUMN IF NOT EXISTS amount_received NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS change_given    NUMERIC(10,2),
    -- A qué turno de caja pertenece (nulo en ventas en línea).
    ADD COLUMN IF NOT EXISTS cash_session_id VARCHAR(50) REFERENCES cash_sessions(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS ix_orders_cash_session ON orders (cash_session_id);

-- El total ya no es solo subtotal + envío: ahora el descuento resta y la
-- propina suma. Las filas anteriores (descuento y propina = 0) lo cumplen.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_total_matches_breakdown,
    ADD  CONSTRAINT orders_total_matches_breakdown
         CHECK (total = subtotal - discount_amount + shipping_cost + tip_amount);

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_discount_valid,
    ADD  CONSTRAINT orders_discount_valid
         CHECK (discount_amount >= 0 AND discount_amount <= subtotal);

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_tip_valid,
    ADD  CONSTRAINT orders_tip_valid
         CHECK (tip_amount >= 0);

-- El cambio siempre es lo recibido menos lo cobrado, y nunca negativo (no se
-- puede cobrar de menos). Recibido y cambio van juntos o ninguno.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_cash_tender_valid,
    ADD  CONSTRAINT orders_cash_tender_valid
         CHECK ((amount_received IS NULL AND change_given IS NULL)
                OR (amount_received IS NOT NULL AND change_given IS NOT NULL
                    AND change_given >= 0
                    AND change_given = amount_received - total));

-- ── Productos: SKU y código de barras ───────────────────────────────────────
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS sku     VARCHAR(64),
    ADD COLUMN IF NOT EXISTS barcode VARCHAR(64);

-- Únicos por negocio (dos negocios pueden usar el mismo código) y solo entre
-- productos vigentes: al borrar un producto su código queda libre. El SKU no
-- distingue mayúsculas, porque "ab-01" y "AB-01" son el mismo para quien teclea.
CREATE UNIQUE INDEX IF NOT EXISTS uq_products_tenant_sku
    ON products (tenant_id, LOWER(sku))
    WHERE sku IS NOT NULL AND is_deleted = FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_products_tenant_barcode
    ON products (tenant_id, barcode)
    WHERE barcode IS NOT NULL AND is_deleted = FALSE;

COMMENT ON TABLE  cash_sessions                 IS 'Turnos de caja: apertura, cierre y diferencia entre lo esperado y lo contado';
COMMENT ON COLUMN cash_sessions.expected_cash   IS 'Fondo inicial + ventas pagadas en efectivo del turno (propinas incluidas)';
COMMENT ON COLUMN cash_sessions.difference      IS 'Contado - esperado: negativo = faltante, positivo = sobrante';
COMMENT ON COLUMN orders.discount_amount        IS 'Descuento aplicado sobre el subtotal de productos (no al envío)';
COMMENT ON COLUMN orders.tip_amount             IS 'Propina; se suma al total y va en el cajón si se pagó en efectivo';
COMMENT ON COLUMN orders.amount_received        IS 'Efectivo entregado por el cliente (solo pagos en efectivo)';
COMMENT ON COLUMN orders.change_given           IS 'Cambio devuelto = amount_received - total';
COMMENT ON COLUMN products.sku                  IS 'Clave interna del negocio; opcional, única entre sus productos vigentes';
COMMENT ON COLUMN products.barcode              IS 'Código de barras (EAN/UPC o interno); opcional, único entre sus productos vigentes';

COMMIT;
