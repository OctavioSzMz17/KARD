-- ============================================================================
-- 010 · Cobro idempotente
--
-- Un cobro del punto de venta puede llegar dos veces: doble clic, un reintento
-- del navegador tras perder la señal, o una respuesta que se quedó en el camino
-- mientras la venta SÍ se guardó. Sin protección eso son dos ventas, el
-- inventario descontado dos veces y el corte de caja con dinero que no existe.
--
-- La solución es la llave de idempotencia: el cliente genera una llave por
-- ticket y la manda con el cobro (cabecera `Idempotency-Key`). El servidor la
-- guarda junto a la venta; si la misma llave vuelve con la misma petición,
-- devuelve la venta ya hecha en vez de crear otra.
--
--   · idempotency_key  la llave que mandó el cliente.
--   · request_hash     huella (SHA-256) de la petición y de quién cobra: si la
--                      misma llave llega con OTRO contenido, es un error del
--                      cliente y se rechaza en vez de contestar con una venta
--                      distinta de la que pidió.
--
-- Migración ADITIVA: ambas columnas son opcionales, así que las ventas
-- existentes y los clientes que aún no mandan llave siguen funcionando.
--
-- Ejecutar en el SQL Editor de Neon. Es idempotente.
-- ============================================================================

BEGIN;

ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(80),
    ADD COLUMN IF NOT EXISTS request_hash    VARCHAR(64);

-- Llave y huella van juntas o ninguna: una sin la otra no permitiría
-- distinguir un reintento de una petición distinta.
ALTER TABLE orders
    DROP CONSTRAINT IF EXISTS orders_idempotency_complete,
    ADD  CONSTRAINT orders_idempotency_complete
         CHECK ((idempotency_key IS NULL AND request_hash IS NULL)
                OR (idempotency_key IS NOT NULL AND request_hash IS NOT NULL));

-- La llave es única POR NEGOCIO: dos negocios pueden (por casualidad) generar
-- la misma, y una no debe poder leer ni bloquear las ventas de la otra. Esta es
-- la red de seguridad final: aunque dos peticiones idénticas llegaran en el
-- mismo instante, la base solo deja guardar una.
CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_tenant_idempotency_key
    ON orders (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

COMMENT ON COLUMN orders.idempotency_key IS 'Llave que el cliente manda con el cobro; el mismo cobro repetido devuelve esta venta en vez de crear otra';
COMMENT ON COLUMN orders.request_hash    IS 'SHA-256 de la petición y de quién cobra: detecta una llave reutilizada con contenido distinto';

COMMIT;
