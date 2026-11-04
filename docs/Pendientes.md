# Pendientes de KARD

Lista de lo que quedó sin hacer al cierre de la sesión del 2 de noviembre de 2026, antes de la última petición (subir a git). Lo ya subido a `main` aparece al final, para tener el punto de partida.

## Ya subido a `main`

- **Backend `e617292`**: cobro idempotente. Llave `Idempotency-Key` por ticket; el mismo cobro devuelve la venta ya hecha (200 + `Idempotent-Replay: true`) sin volver a descontar inventario; la misma llave con otro contenido o de otra persona da 409 `IDEMPOTENCY_CONFLICT`; una llave con mala forma da 422. Incluye la migración `010_cobro_idempotente.sql`, ya aplicada a Neon.
- **Cliente `a201246`**: un empleado que entra por el login normal ya no es desviado a "Autorizar caja". Cobrar muestra el aviso "Este equipo no es una caja autorizada" y deshabilita el botón. La pantalla de autorizar y desbloquear solo aparece desde "Modo caja". Prueba PC-13.

## Orden acordado

El orden fue **2 → 1 → (3 se omite) → 5 → 4**, más el 11.

### 2. Nombre del cajero en el ticket y en el sistema
- **Estado:** esperando tu aprobación. No se ha programado.
- **Propuesta:** en el ticket impreso, `Caja: Caja 1` y `Atendió: Eva P.` (primer nombre y inicial del apellido). El código de empleado y el nombre completo solo en vistas internas.
- **Hoy:** `orders/routes.py` pone `cashier=current_user.email.split("@")[0]`, es decir, el correo sin dominio.
- **Pendiente:** decidir el formato y, al aprobarlo, cambiar el origen del nombre a `full_name`, agregar la caja al ticket y mostrar el cajero en el reporte de ventas y en el corte por cajero.

### 1. Idempotencia del cobro
- **Backend:** hecho (ver arriba). Archivos: `orders/idempotency.py`, `orders/service.py` (`create_pos_sale_idempotent`, `_replay`), `orders/repository.py` (candado `pg_advisory_xact_lock` y búsqueda por llave), `orders/routes.py`, `core/exceptions.py` (`IdempotencyConflictError`), `main.py` (handler 409), `tests/test_cobro_idempotente.py` (418 pruebas pasando en total).
- **Falta en el cliente:** generar una llave por ticket en `PosComponent` y mandarla en `SalesService.charge` como cabecera `Idempotency-Key`. La llave debe reutilizarse en reintentos del mismo ticket y generarse nueva solo cuando el ticket cambie o se cobre. Si el servidor responde 409 `IDEMPOTENCY_CONFLICT`, avisar al cajero y no reintentar a ciegas.
- **Falta en la verificación:**
  - Comprobación de mutaciones: **hecha**. Las diez fallas reintroducidas se detectan (se corrió sobre una copia del proyecto).
  - Prueba de carrera contra la base real (`tests/integracion/e2e_idempotencia.py`): dos peticiones simultáneas con la misma llave deben producir una sola venta y un solo descuento de inventario.
- **Documentación:** **hecha** (PS-07, PC-13 y PC-14 en `docs/Pruebas_IEEE829.md`, con sus conteos).

### 5. Varias cajas registradoras
- **Estado:** pendiente de diseño e implementación.
- **Hoy:** solo puede haber un turno abierto por negocio (índice único parcial `uq_cash_sessions_one_open` sobre `cash_sessions(tenant_id)`). Dos cajas comparten un solo cajón.
- **Lo que pediste:** si un equipo autorizado intenta abrir caja mientras ya hay una activa, se crea automáticamente "Caja 2" con su propio turno y sus propios datos, para que no se crucen.
- **Puntos a diseñar:**
  - Cambiar la unicidad de "uno por negocio" a "uno por caja abierta".
  - Asociar cada turno a un equipo autorizado.
  - Corte, efectivo esperado y diferencias por caja.
  - Que el ticket muestre el nombre de la caja.
  - Qué pasa con el cierre de caja de un equipo, que hoy revoca todos los equipos del negocio.

### 4. Celular como escáner sincronizado con el ticket de la caja
- **Estado:** pendiente. Depende de la 5.
- **Diseño discutido:**
  - El ticket en borrador vive en el servidor, no en el navegador.
  - Un QR de emparejamiento bajo la pestaña "Ticket" de la caja.
  - Una lámpara que indique si el celular está conectado.
  - El celular solo escanea y agrega productos; no cobra.
  - Eventos idempotentes, para que un escaneo repetido no duplique líneas.
  - Mismas reglas de seguridad que el acceso por QR: el secreto solo vive en el equipo, y el servidor guarda su hash.
- **Diseño:** escrito en `docs/Diseno_Ticket_Sincronizado.md` (ticket en Redis con TTL renovable, copia en Postgres, SSE y emparejamiento por QR). Decidido: el celular solo escanea y se sincroniza con el modo caja, y el ticket se vacía cuando entra otra persona. Quedan abiertas tres decisiones menores (sección 11).
- **Resuelto:** cobrar ya no depende del equipo autorizado, así que el cobro desde el celular con sesión de empleado es válido.

### 11. Spanglish en la interfaz
- **Estado:** pendiente.
- **Alcance:** textos en inglés en Products, Inventory, Customers, Audit Log, Dashboard y otros que aparezcan en la revisión.

### 3. Base de datos
- **Decisión tuya:** vas a borrar la base actual y crear una nueva con las ramas adecuadas. Ya no es tarea nuestra por ahora. Cuando lo hagas, hay que volver a aplicar las migraciones 001 a 010 y correr los seeders.

## Diferidos por ti

- **6. Modo punto de venta / tienda en línea:** un interruptor en la barra superior para cambiar entre vistas. Espera a que apruebes el diseño.
- **7. Control de planes (plan gating):** "al final".
- **8 y 9. Gráficas del dashboard:** hoy son datos de ejemplo; pendientes de conectarse a datos reales.
- **10. Reporte CSV / PDF:** agregar propinas, descuentos y forma de pago.
- **12 a 19. Revisiones manuales:** cambio de contraseña y recuperación, correo opcional, sucursales o franquicias, nota de geolocalización del equipo, prueba con cámara real, impresión e iPhone, y actualizar la documentación HTML y la presentación.
  - *Nota:* el resumen de la sesión no detalla el número de cada punto de este bloque. Confirmar con el usuario antes de tomar uno.

## Otros pendientes técnicos

- **Modo caja en el celular:** en pruebas, un navegador de celular que abre `/caja-registradora` debe ver el mismo flujo de autorización; no se ha probado en un iPhone real.
- **Git:** los commits de hoy se hicieron con `Co-Authored-By` de Sonnet 5.5. Las advertencias de LF/CRLF al hacer `git add` son solo del entorno de Windows; no cambian el contenido.

## Acceso del empleado (hecho el 3 de noviembre de 2026)

- Cobrar **ya no exige un equipo autorizado**: cualquier puesto con permiso de cobrar lo hace desde su propio celular o computadora. El equipo autorizado solo existe para la pantalla de la caja registradora (modo caja). Se quitaron `assert_can_charge` y `needs_authorized_device` del servidor.
- El empleado que entra por el login normal cae en **Cobrar**, con su menú: Cobrar, Escanear y Productos.
- Se **eliminó "Bloquear caja"** de todas las vistas, junto con el bloqueo por inactividad. Para cambiar de persona se cierra sesión: en una caja autorizada deja la caja en su pantalla de entrada.
- Las comprobaciones de punta a punta contra la base real (`e2e_equipo.py`) suponen una base limpia (códigos de empleado desde 0002 y sin equipos previos); con tus datos de prueba reales cinco de ellas fallan por eso. Se corrigen al recrear la base.
- Hay archivos antiguos sin versionar que reaparecieron (probablemente por OneDrive): `Kard/logging/` y, en el cliente, `src/app/auth/login/`, `src/app/auth/register/` y `src/app/auth/auth-shared.scss`. Son copias exactas de versiones que ya borraron commits anteriores. `logging/` tapa a la librería estándar de Python (rompe `python -m pytest` y el contenedor), y los del cliente rompen `ng build` y `ng test`. Se pueden borrar sin pérdida: están en el historial de git.
