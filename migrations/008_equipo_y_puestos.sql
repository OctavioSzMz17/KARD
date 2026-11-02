-- =============================================================
-- 008 — Equipo del negocio: nombre, puesto y código de empleado
--
-- Solo aditiva (la base de Render comparte Neon): agrega columnas y un
-- índice, no cambia ni quita nada de lo que el código anterior usa.
--
-- Los roles de la base (`business_admin`, `staff`) NO cambian. El puesto es
-- una columna aparte que identifica a la persona dentro del negocio:
--   business_admin            → dueño
--   staff + gerente           → gerente
--   staff + subgerente        → subgerente
--   staff + encargado_area    → encargado de área
--   staff sin puesto          → empleado
-- =============================================================

ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name     VARCHAR(120);
ALTER TABLE users ADD COLUMN IF NOT EXISTS job_title     VARCHAR(30);
ALTER TABLE users ADD COLUMN IF NOT EXISTS employee_code VARCHAR(12);

-- El puesto solo existe para el personal (`staff`); el dueño no lleva puesto.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_job_title_valid;
ALTER TABLE users ADD CONSTRAINT users_job_title_valid CHECK (
    job_title IS NULL
    OR (job_title IN ('gerente', 'subgerente', 'encargado_area') AND role::text = 'staff')
);

-- Código de empleado para quienes ya existen: 0001, 0002… por negocio, en
-- orden de alta (el dueño, que se registró primero, queda con 0001).
-- Los administradores de la plataforma no pertenecen a un negocio: se omiten.
WITH numerados AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY tenant_id ORDER BY created_at, id) AS n
    FROM users
    WHERE role::text <> 'saas_admin'
)
UPDATE users u
SET    employee_code = LPAD(numerados.n::text, 4, '0')
FROM   numerados
WHERE  u.id = numerados.id AND u.employee_code IS NULL;

-- El código es único DENTRO de cada negocio (dos negocios pueden tener un 0001).
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_tenant_employee_code
    ON users (tenant_id, employee_code)
    WHERE employee_code IS NOT NULL;

COMMENT ON COLUMN users.full_name     IS 'Nombre con el que aparece en tickets y cortes';
COMMENT ON COLUMN users.job_title     IS 'Puesto dentro del negocio: gerente | subgerente | encargado_area. NULL = empleado (o dueño)';
COMMENT ON COLUMN users.employee_code IS 'Código de empleado generado por el sistema, único por negocio';
