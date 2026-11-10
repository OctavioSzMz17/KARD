-- ============================================================================
-- 013 · Recuperar la contraseña por correo
--
-- Cada persona puede restablecer su propia contraseña desde su correo (dueño,
-- gerencia, empleados y también los clientes de la tienda), sin molestar al resto
-- del equipo.
--
--   · password_resets
--       Un registro por enlace enviado. Del enlace se guarda SOLO su huella
--       (SHA-256), igual que el token de una caja registradora: una filtración de
--       la base no entrega enlaces que se puedan usar. Cada enlace sirve una sola
--       vez y caduca (`expires_at`).
--
--   · users.password_changed_at / consumers.password_changed_at
--       Cuándo se cambió por última vez la contraseña por esta vía. Una sesión
--       iniciada ANTES de ese momento deja de valer: si alguien tenía robada la
--       sesión, restablecer la contraseña se la quita.
--
-- Migración ADITIVA: todo es opcional, lo anterior sigue funcionando.
--
-- Ejecutar en el SQL Editor de Neon. Es idempotente.
-- ============================================================================

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

ALTER TABLE consumers
    ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS password_resets (
    id            VARCHAR(40)  PRIMARY KEY,
    -- A quién pertenece el enlace: una persona del negocio o un cliente de la tienda.
    account_kind  VARCHAR(10)  NOT NULL,
    account_id    VARCHAR(50)  NOT NULL,
    token_hash    VARCHAR(64)  NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ  NOT NULL,
    used_at       TIMESTAMPTZ,
    requested_ip  VARCHAR(64),

    CONSTRAINT password_resets_kind_valid CHECK (account_kind IN ('user', 'consumer'))
);

-- Buscar y anular los enlaces abiertos de una cuenta.
CREATE INDEX IF NOT EXISTS ix_password_resets_account ON password_resets (account_kind, account_id);
CREATE INDEX IF NOT EXISTS ix_password_resets_expires ON password_resets (expires_at);

COMMENT ON TABLE  password_resets                     IS 'Enlaces para restablecer la contraseña por correo; se guarda solo la huella del enlace';
COMMENT ON COLUMN password_resets.token_hash          IS 'SHA-256 del enlace; el enlace en sí solo viaja en el correo';
COMMENT ON COLUMN users.password_changed_at           IS 'Última vez que se restableció la contraseña por correo; las sesiones anteriores dejan de valer';
COMMENT ON COLUMN consumers.password_changed_at       IS 'Última vez que se restableció la contraseña por correo; las sesiones anteriores dejan de valer';

COMMIT;
