-- =============================================================
-- Migration 004 — product_recipes (recetas / Bill of Materials)
-- Run once on Neon after migration 003.
--
-- Enlaza un producto final (platillo) con sus insumos (ingredientes).
-- Al vender el platillo se descuenta su stock y el de cada insumo.
-- =============================================================

CREATE TABLE product_recipes (
    id                 VARCHAR(50)  PRIMARY KEY,
    tenant_id          VARCHAR(50)  NOT NULL REFERENCES tenants(id)  ON DELETE RESTRICT,
    final_product_id   VARCHAR(60)  NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    insumo_product_id  VARCHAR(60)  NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity           INTEGER      NOT NULL CHECK (quantity > 0),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Un insumo no puede estar dos veces en la misma receta
    CONSTRAINT uq_recipe_final_insumo UNIQUE (final_product_id, insumo_product_id)
);

CREATE INDEX idx_recipes_final   ON product_recipes(final_product_id);
CREATE INDEX idx_recipes_tenant  ON product_recipes(tenant_id);

COMMENT ON TABLE  product_recipes                  IS 'Receta: insumos que consume un producto final al venderse';
COMMENT ON COLUMN product_recipes.quantity         IS 'Unidades del insumo consumidas por cada unidad del producto final';
