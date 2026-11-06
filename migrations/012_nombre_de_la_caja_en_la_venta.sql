-- ============================================================================
-- 012 · Nombre de la caja en la venta
--
-- El ticket y el reporte de ventas dicen DESDE QUÉ CAJA se cobró ("Caja 1"). Se
-- guarda el nombre tal como era en ese momento (una copia, no una referencia):
-- si mañana la gerencia autoriza otra computadora con el mismo nombre, o la caja
-- se desautoriza, las ventas de hoy deben seguir diciendo la caja de entonces.
--
-- Una venta cobrada desde el celular de quien opera una caja también lleva el
-- nombre de esa caja. Las ventas en línea y las anteriores a esta migración
-- quedan sin nombre de caja.
--
-- Migración ADITIVA: la columna es opcional.
--
-- Ejecutar en el SQL Editor de Neon. Es idempotente.
-- ============================================================================

BEGIN;

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS register_name VARCHAR(60);

COMMENT ON COLUMN orders.register_name IS 'Nombre de la caja desde la que se cobró (copia del momento de la venta); nulo en ventas en línea y anteriores a la migración 012';

COMMIT;
