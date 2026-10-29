-- ============================================================================
-- 005 · Punto de venta y tipos de entrega
--
-- Habilita dos cosas que hoy el esquema impide:
--
--   1. Ventas de mostrador. `consumer_id` era NOT NULL, así que toda venta
--      exigía una cuenta de cliente registrada: un comprador que paga en
--      efectivo y se va no cabía en la tabla.
--
--   2. Entregas. Un pedido ahora dice CÓMO llega al cliente (en la mesa,
--      se lo lleva, lo recoge después, o se le envía) y lleva los datos
--      que cada caso necesita.
--
-- Dos ejes independientes:
--   captured_by       quién capturó la venta  → staff | self
--   fulfillment_type  cómo la recibe el cliente → dine_in | takeaway | pickup | delivery
--
-- Y dos estados separados, porque son ortogonales: en una mesa se pide
-- primero y se paga al final, así que un pedido puede estar servido y sin
-- pagar. `status` queda como estado de PAGO; `fulfillment_status` es el de
-- ENTREGA.
--
-- Migración aditiva: no borra datos ni rompe filas existentes.
-- Ejecutar en el SQL Editor de Neon.
-- ============================================================================

BEGIN;

-- ── 1. Una venta puede no tener cliente registrado ─────────────────────────
ALTER TABLE orders ALTER COLUMN consumer_id DROP NOT NULL;

-- ── 2. Origen y tipo de entrega ────────────────────────────────────────────
-- Los pedidos que ya existen vinieron del marketplace (los capturó el propio
-- cliente) y se entregaban recogiendo en tienda, que era el único modo que
-- había. Esos son los valores por defecto para no inventar historia.
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS captured_by      VARCHAR(10)  NOT NULL DEFAULT 'self',
    ADD COLUMN IF NOT EXISTS fulfillment_type VARCHAR(20)  NOT NULL DEFAULT 'pickup';

-- ── 3. Estado de entrega, separado del de pago ─────────────────────────────
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS fulfillment_status VARCHAR(20) NOT NULL DEFAULT 'preparing';

-- ── 4. Cómo se pagó y quién cobró ──────────────────────────────────────────
-- `payment_method` queda NULL en los pedidos viejos: no se registraba, y
-- rellenarlo con un valor inventado sería peor que admitir que no se sabe.
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS payment_method VARCHAR(20),
    ADD COLUMN IF NOT EXISTS user_id        VARCHAR(50) REFERENCES users(id) ON DELETE SET NULL;

-- ── 5. Desglose del total ──────────────────────────────────────────────────
-- El envío no es ingreso por productos. Mezclarlo en `total` inflaría el
-- reporte de ventas, así que el subtotal vive aparte.
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS subtotal      NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS shipping_cost NUMERIC(10,2) NOT NULL DEFAULT 0;

-- Los pedidos existentes no tenían envío: su subtotal es su total.
UPDATE orders SET subtotal = total WHERE subtotal IS NULL;
ALTER TABLE orders ALTER COLUMN subtotal SET NOT NULL;

-- ── 6. Datos de la entrega ─────────────────────────────────────────────────
-- La dirección va COPIADA dentro del pedido, no referenciada: si el cliente
-- edita su dirección en marzo, el pedido de enero debe seguir diciendo a
-- dónde se mandó. Es el mismo criterio con el que `order_items` congela
-- `product_name` y `unit_price`.
ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS shipping_address JSONB,
    ADD COLUMN IF NOT EXISTS table_number     VARCHAR(20),
    ADD COLUMN IF NOT EXISTS customer_name    VARCHAR(120);

-- ── 7. Integridad ──────────────────────────────────────────────────────────
-- Valores permitidos. Se usan CHECK sobre VARCHAR en vez de tipos ENUM de
-- Postgres para poder añadir un modo de entrega sin un ALTER TYPE.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_captured_by_valid,
    ADD  CONSTRAINT orders_captured_by_valid
         CHECK (captured_by IN ('staff', 'self'));

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_fulfillment_type_valid,
    ADD  CONSTRAINT orders_fulfillment_type_valid
         CHECK (fulfillment_type IN ('dine_in', 'takeaway', 'pickup', 'delivery'));

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_fulfillment_status_valid,
    ADD  CONSTRAINT orders_fulfillment_status_valid
         CHECK (fulfillment_status IN ('preparing', 'ready', 'in_transit', 'completed', 'canceled'));

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_payment_method_valid,
    ADD  CONSTRAINT orders_payment_method_valid
         CHECK (payment_method IS NULL OR payment_method IN ('cash', 'card', 'transfer', 'online'));

-- `status` pasa a significar solo el estado de PAGO, y se le suma 'refunded'.
-- Se conserva 'fulfilled' porque hay ventas históricas con ese valor: quitarlo
-- haría fallar la constraint sobre filas que ya existen.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS chk_order_status,
    ADD  CONSTRAINT chk_order_status
         CHECK (status IN ('pending', 'paid', 'refunded', 'fulfilled', 'canceled'));

-- La aritmética no puede desviarse, igual que en inventory_transactions.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_total_matches_breakdown,
    ADD  CONSTRAINT orders_total_matches_breakdown
         CHECK (total = subtotal + shipping_cost);

ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_amounts_non_negative,
    ADD  CONSTRAINT orders_amounts_non_negative
         CHECK (subtotal >= 0 AND shipping_cost >= 0);

-- Una venta la capturó un empleado o un cliente, y en cada caso se sabe quién.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_has_an_author,
    ADD  CONSTRAINT orders_has_an_author
         CHECK (consumer_id IS NOT NULL OR user_id IS NOT NULL);

-- ── 8. Índices para el reporte ─────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_orders_tenant_fulfillment
    ON orders(tenant_id, fulfillment_type);

CREATE INDEX IF NOT EXISTS idx_orders_tenant_channel
    ON orders(tenant_id, captured_by);

-- ── 9. Documentación ───────────────────────────────────────────────────────
COMMENT ON COLUMN orders.captured_by        IS 'staff = empleado en el POS · self = el cliente desde su dispositivo';
COMMENT ON COLUMN orders.fulfillment_type   IS 'dine_in | takeaway | pickup | delivery';
COMMENT ON COLUMN orders.fulfillment_status IS 'Estado de ENTREGA, independiente del de pago (orders.status)';
COMMENT ON COLUMN orders.status             IS 'Estado de PAGO: pending | paid | refunded | canceled';
COMMENT ON COLUMN orders.shipping_address   IS 'Copia de la direccion al momento de la compra, no referencia';
COMMENT ON COLUMN orders.table_number       IS 'Mesa, solo para dine_in';
COMMENT ON COLUMN orders.customer_name      IS 'Nombre para el ticket cuando no hay cuenta registrada';
COMMENT ON COLUMN orders.subtotal           IS 'Suma de los articulos, sin envio';

COMMIT;
