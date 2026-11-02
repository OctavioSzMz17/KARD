-- =============================================================
-- 009 — Equipos autorizados y acceso por QR
--
-- Solo aditiva (la base de Render comparte Neon): crea dos tablas nuevas; no
-- toca nada de lo que el código anterior usa.
--
-- `devices`        Una computadora que quien está a cargo autorizó para el día
--                  (una caja registradora). Se identifica con un token que solo
--                  vive en ese navegador; aquí se guarda únicamente su HASH.
-- `auth_challenges` Un QR en pantalla esperando que un celular lo apruebe:
--                  `enroll` autoriza una computadora; `unlock` abre sesión de
--                  una persona en un equipo que ya está autorizado.
-- =============================================================

CREATE TABLE IF NOT EXISTS devices (
    id            VARCHAR(40)  PRIMARY KEY,
    tenant_id     VARCHAR(50)  NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    name          VARCHAR(60)  NOT NULL,
    token_hash    CHAR(64)     NOT NULL UNIQUE,
    authorized_by VARCHAR(50)  REFERENCES users(id) ON DELETE SET NULL,
    user_agent    VARCHAR(300),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ  NOT NULL,
    revoked_at    TIMESTAMPTZ,
    last_seen_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_devices_tenant_active
    ON devices (tenant_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS auth_challenges (
    id                VARCHAR(40)  PRIMARY KEY,
    kind              VARCHAR(10)  NOT NULL CHECK (kind IN ('enroll', 'unlock')),
    -- `enroll` nace sin negocio (la computadora aún no es de nadie): lo fija quien aprueba.
    tenant_id         VARCHAR(50)  REFERENCES tenants(id) ON DELETE CASCADE,
    device_id         VARCHAR(40)  REFERENCES devices(id) ON DELETE CASCADE,
    -- Hash del secreto que solo conoce la computadora que muestra el QR.
    secret_hash       CHAR(64)     NOT NULL,
    -- Código de 4 dígitos que se ve en la computadora y se teclea en el celular.
    verification_code CHAR(4)      NOT NULL,
    attempts          INT          NOT NULL DEFAULT 0,
    user_agent        VARCHAR(300),
    ip_address        VARCHAR(64),
    status            VARCHAR(10)  NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'claimed', 'denied')),
    approved_by       VARCHAR(50)  REFERENCES users(id) ON DELETE CASCADE,
    device_name       VARCHAR(60),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at        TIMESTAMPTZ  NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_auth_challenges_expires ON auth_challenges (expires_at);

COMMENT ON TABLE  devices                   IS 'Computadoras autorizadas por la gerencia para el día';
COMMENT ON COLUMN devices.token_hash        IS 'SHA-256 del token del equipo; el token en claro solo existe en su navegador';
COMMENT ON TABLE  auth_challenges           IS 'QR pendientes de aprobar desde un celular (autorizar equipo o abrir sesión)';
COMMENT ON COLUMN auth_challenges.secret_hash IS 'SHA-256 del secreto de la computadora; solo quien lo tiene puede reclamar el resultado';
