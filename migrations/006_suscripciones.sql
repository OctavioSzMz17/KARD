-- ============================================================================
-- 006 · Suscripciones (Stripe)
--
-- El negocio le paga a KARD una mensualidad por usar la plataforma. Esta
-- migración guarda el vínculo con Stripe y el estado de esa suscripción.
--
-- Por qué estos campos y no otros: Stripe es la fuente de verdad del cobro,
-- así que aquí solo se guarda lo mínimo para (a) reconocer al negocio en
-- Stripe y (b) poder decidir si tiene acceso sin consultar su API en cada
-- request. Importes, facturas e historial viven en Stripe.
--
-- Migración aditiva. Ejecutar en el SQL Editor de Neon.
-- ============================================================================

BEGIN;

ALTER TABLE tenants
    -- Identificadores de Stripe. Nulos mientras el negocio no haya pagado nunca.
    ADD COLUMN IF NOT EXISTS stripe_customer_id     VARCHAR(60),
    ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(60),

    -- Espejo del estado de Stripe. 'none' = nunca contrató.
    ADD COLUMN IF NOT EXISTS subscription_status    VARCHAR(30) NOT NULL DEFAULT 'none',

    -- Plan contratado: starter | pro | enterprise.
    ADD COLUMN IF NOT EXISTS plan                   VARCHAR(20),

    -- Hasta cuándo está pagado. Permite seguir dando acceso durante el
    -- periodo ya cobrado aunque la renovación falle después.
    ADD COLUMN IF NOT EXISTS current_period_end     TIMESTAMPTZ;

-- Los estados son los de Stripe, más 'none' para quien nunca contrató.
ALTER TABLE tenants
    DROP CONSTRAINT IF EXISTS tenants_subscription_status_valid,
    ADD  CONSTRAINT tenants_subscription_status_valid
         CHECK (subscription_status IN (
             'none', 'trialing', 'active', 'past_due',
             'canceled', 'incomplete', 'incomplete_expired', 'unpaid', 'paused'
         ));

ALTER TABLE tenants
    DROP CONSTRAINT IF EXISTS tenants_plan_valid,
    ADD  CONSTRAINT tenants_plan_valid
         CHECK (plan IS NULL OR plan IN ('starter', 'pro', 'enterprise'));

-- Un cliente de Stripe pertenece a un solo negocio: si dos filas apuntaran al
-- mismo, un webhook no sabría a cuál aplicar el cambio.
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_stripe_customer
    ON tenants(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_stripe_subscription
    ON tenants(stripe_subscription_id) WHERE stripe_subscription_id IS NOT NULL;

-- ── Eventos de Stripe ya procesados ────────────────────────────────────────
-- Stripe reintenta un webhook hasta que responde 2xx, y puede entregar el
-- mismo evento más de una vez. Sin esta tabla, un reintento volvería a
-- aplicar el cambio; con ella, el segundo intento se reconoce y se ignora.
CREATE TABLE IF NOT EXISTS stripe_events (
    id           VARCHAR(80)  PRIMARY KEY,   -- evt_... tal cual lo manda Stripe
    type         VARCHAR(80)  NOT NULL,
    processed_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  stripe_events                 IS 'Eventos ya aplicados, para que un reintento de Stripe no duplique el efecto';
COMMENT ON COLUMN tenants.subscription_status   IS 'Espejo del estado en Stripe; none = nunca contrato';
COMMENT ON COLUMN tenants.current_period_end    IS 'Hasta cuando esta pagado el periodo vigente';

COMMIT;
