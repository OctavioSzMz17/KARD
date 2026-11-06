# Pendientes de KARD

Actualizado el 5 de noviembre de 2026.

## Alcance de la primera versión

- **Una sola caja por negocio** (decidido). Dos computadoras autorizadas comparten el mismo turno de efectivo; las "cajas múltiples" quedan para después.
- **Sin control de planes** por ahora: todos los planes hacen lo mismo.
- **Cobrar no depende del equipo**: cualquier puesto con permiso de cobrar lo hace desde su celular o computadora. El equipo autorizado solo sirve para la pantalla de la caja registradora.

## Hecho y ya en `main`

- **Cobro idempotente** (migración `010`): una llave por ticket; el mismo cobro devuelve la venta ya hecha sin duplicarla.
- **Acceso del empleado**: entra por el login normal a su panel; se quitó "Bloquear caja".
- **Ticket compartido caja ↔ celular** (migración `011`): lo que se escanea en el celular aparece en la computadora de la misma caja y al revés; cualquiera de las dos puede cobrar. Detalle en `docs/Diseno_Ticket_Sincronizado.md`.

## Hecho, todavía sin subir: el "lote rápido" de la primera versión

- **Quién cobró y desde qué caja** (migración `012`, ya aplicada a Neon).
  - El **ticket del cliente** dice `Caja: Caja 1` y `Atendió: Eva P.` (nombre y la inicial del último apellido; nunca el correo ni el nombre completo).
  - El **reporte de ventas** tiene la columna **Cajero** (nombre completo, código y caja) y el CSV trae *Cajero* y *Caja*.
  - El **corte de caja** muestra **Ventas por cajero**, en pantalla y en la hoja impresa; el corte cerrado lo congela. Los cortes anteriores a esto se leen igual, sin esa sección.
  - Una venta desde el celular de quien opera una caja lleva el nombre de esa caja, igual que una cobrada en la computadora.
- **Dashboard con ventas reales.** La línea inventada con `Math.random()` desapareció. Ahora hay "Ventas de los últimos 7 días" (total, número de ventas, ticket promedio y la gráfica por día) y "Más vendidos (7 días)". Los días son los del negocio (el navegador manda su zona horaria): una venta de las 9 de la noche no cae en el día siguiente. Solo cuentan las ventas cobradas (pagadas o entregadas); las canceladas no.
  - Se quitó el trazo decorativo del "Resumen de Inventario" y la "Tendencia de Stock", que no tenían datos reales detrás.
- **Todo en español.** Menú: *Inicio, Productos, Inventario*; títulos de página; y los mensajes de error del servidor que llegaban tal cual a la pantalla ("Product not found", "Invalid email or password", etc.). Se quitaron del menú *Customers* y *Audit Log*, que no tenían pantalla.
  - Se dejó **"stock"** (como en "Stock bajo"): es la palabra que se usa en México. Si prefieres "existencias", es un cambio de textos.

## Para salir a producción

- Las migraciones `010`, `011` y `012` **ya están en Neon**. El servidor de Render funciona con ellas aunque todavía no tenga el código nuevo.
- **Falta probar el camino con Redis contra el Redis de Render.** Solo se probó con un Redis simulado que respeta `WATCH/MULTI`; sin Redis el ticket vive en Postgres y eso sí se probó contra la base real. Conviene confirmar si el Redis de Render guarda datos en disco: la copia en Postgres cubre el caso de que no.

## Lo que falta para cerrar la primera versión (en este orden)

1. **Recuperar contraseña.** El enlace "¿Olvidaste tu contraseña?" del login es un `href="#"` sin función y el servidor no tiene endpoint. La gerencia sí puede restablecerle la contraseña a un empleado, pero **el dueño no puede recuperar la suya**. Resend ya está integrado para otros correos.
2. **Borrar los archivos viejos que reaparecieron** (probablemente por OneDrive) y agregar un **script que aplique las migraciones en orden** (001 a 012) para la base nueva.
   - `Kard/logging/` y, en el cliente, `src/app/auth/login/`, `src/app/auth/register/` y `src/app/auth/auth-shared.scss`. Son copias exactas de versiones que ya borraron commits anteriores. `logging/` tapa a la librería estándar de Python (rompe `python -m pytest` y el contenedor); los del cliente rompen `ng build` y `ng test`. Se pueden borrar sin pérdida: están en el historial de git. Mientras estén, las pruebas se corren con `python -P` y apartándolos un momento.
3. **Desplegar y probar con tus aparatos** (tablet, celular, laptop) con el servidor y el cliente nuevos en Render.
4. **Documentación y presentación**: el HTML y las diapositivas no reflejan la caja, los equipos, el ticket compartido ni el Dashboard real.

## Después de la primera versión

- **Cajas múltiples con su propio turno** (la "Caja 2"). Hoy hay un solo turno por negocio (índice único parcial `uq_cash_sessions_one_open`). Hay que decidir unicidad por caja, turno asociado a un equipo, corte por caja, y qué pasa con el cierre, que hoy desautoriza todos los equipos.
- **Control de planes (plan gating).**
- **Reporte CSV / PDF**: agregar propinas, descuentos y forma de pago por venta (hoy el CSV ya trae cajero y caja).
- **Prueba de carrera real de la idempotencia** contra la base con dos conexiones cobrando el mismo ticket a la vez (como `e2e_ticket_concurrencia.py` lo hace con el ticket).
- **Mejoras del ticket compartido**: tiempo real por SSE en lugar de preguntar cada 1.2 s; compartir también descuento, propina, forma de pago y nombre del cliente; límite de operaciones por segundo.
- **Modo punto de venta / tienda en línea**: un interruptor en la barra superior. Espera a que apruebes el diseño.
- **Revisiones manuales:** cambio de contraseña desde dentro de la cuenta, correo opcional, sucursales o franquicias, geolocalización (regla acordada: toda acción permitida mientras sea dentro de la ubicación de confianza; ver `docs/Notas_Futuras.md`), prueba con cámara real, impresión de tickets y iPhone.
  - *Nota:* el resumen de la sesión no detalla el número de cada punto de este bloque. Confirmar antes de tomar uno.

## Base de datos nueva

- **Decisión tuya:** vas a borrar la base actual y crear una nueva con las ramas adecuadas. Hay que volver a aplicar las migraciones **001 a 012** y correr los seeders.
- Las pruebas de punta a punta `e2e_equipo.py` suponen una base limpia (códigos de empleado desde 0002 y sin equipos previos); con tus datos de prueba reales cinco de sus verificaciones fallan por eso.
