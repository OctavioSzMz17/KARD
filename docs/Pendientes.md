# Pendientes de KARD

Actualizado el 4 de noviembre de 2026.

## Hecho y ya en `main`

- **Cobro idempotente** (servidor `e617292`, migración `010`): una llave por ticket (`Idempotency-Key`); el mismo cobro devuelve la venta ya hecha (200) sin volver a descontar inventario; la misma llave con otro contenido o de otra persona da 409.
- **Acceso del empleado** (servidor `ffb6e51`, cliente `de15bcc`): cobrar ya no exige un equipo autorizado; el empleado entra por el login normal a su panel (Cobrar, Escanear, Productos). El equipo autorizado solo sirve para la pantalla de la caja registradora. Se quitó "Bloquear caja" y el bloqueo por inactividad.

## Hecho, todavía sin subir: ticket compartido caja ↔ celular

Lo que se escanea con el celular aparece en la computadora de la misma caja, y lo que se borra en una se borra en la otra. Cualquiera de las dos puede cobrar, y cobrar el mismo ticket en las dos no lo duplica. Detalle en `docs/Diseno_Ticket_Sincronizado.md`.

- **Servidor:** migración `011_ticket_compartido.sql` (**ya aplicada a Neon**), paquete `tickets/`, quién opera cada caja en `devices`, `POST /device-auth/release`, y limpieza de tickets al cerrar la caja.
- **Cliente:** `TicketSyncService` (consulta cada 1.2 s, fila de cambios que sobrevive a un corte de internet), lámpara "Caja 1 · sincronizada" en Cobrar, y la llave de idempotencia del cobro ya viaja en la cabecera.
- **Verificado:** 510 pruebas del servidor y 253 del cliente; recorrido contra la base real (`e2e_ticket.py`) y de concurrencia con cuatro conexiones (`e2e_ticket_concurrencia.py`); y en pantalla (laptop en el navegador y celular simulado). Las mutaciones de servidor y cliente se detectan.

### Para salir a producción
- La migración `011` ya está en Neon. El servidor desplegado en Render funciona con ella aunque todavía no tenga el código nuevo.
- **Falta probar el camino con Redis contra el Redis de Render.** Solo se probó con un Redis simulado que respeta `WATCH/MULTI`. Sin Redis (como en tu máquina) el ticket vive en Postgres, y eso sí se probó contra la base real. Conviene confirmar si el Redis de Render guarda datos en disco: la copia en Postgres cubre el caso de que no.

### Mejoras posibles del ticket compartido (no pedidas)
- Tiempo real por SSE en lugar de preguntar cada 1.2 s.
- Compartir también descuento, propina, forma de pago y nombre del cliente (hoy solo se comparten las líneas).
- Límite de operaciones por segundo por sesión.
- Pasar a Cobrar al empleado desde Escanear con un botón después de "Entrar en esta caja".

## Por hacer, en el orden acordado

### 2. Nombre del cajero en el ticket y en el sistema
- **Estado:** esperando tu aprobación del formato. No se ha programado.
- **Propuesta:** en el ticket impreso, `Caja: Caja 1` y `Atendió: Eva P.` (primer nombre y inicial del apellido). El código de empleado y el nombre completo solo en vistas internas (Ventas y corte por cajero).
- **Hoy:** `orders/routes.py` pone `cashier=current_user.email.split("@")[0]`, es decir, el correo sin dominio.
- **Pendiente:** decidir el formato y, al aprobarlo, usar `full_name`, agregar la caja al ticket y mostrar el cajero en el reporte de ventas y en el corte.

### 5. Varias cajas registradoras con su propio turno
- **Estado:** pendiente de diseño e implementación. El ticket ya es por caja; lo que falta es el efectivo.
- **Hoy:** solo puede haber un turno abierto por negocio (índice único parcial `uq_cash_sessions_one_open`). Dos cajas comparten un solo cajón.
- **Lo que pediste:** si un equipo autorizado intenta abrir caja mientras ya hay una activa, se crea automáticamente "Caja 2" con su propio turno y sus propios datos.
- **Puntos a diseñar:** unicidad "uno por caja abierta", turno asociado a un equipo, corte y diferencias por caja, y qué pasa con el cierre de caja, que hoy desautoriza todos los equipos del negocio.

### 11. Spanglish en la interfaz
- **Estado:** pendiente.
- **Alcance:** textos en inglés en Products, Inventory, Customers, Audit Log, Dashboard y otros que aparezcan en la revisión.

### 1 (resto). Prueba de carrera real de la idempotencia
- El cobro idempotente y su llave en el cliente ya están. Falta una prueba contra la base con **dos conexiones reales** cobrando el mismo ticket a la vez (como `e2e_ticket_concurrencia.py` lo hace con el ticket).

### 3. Base de datos
- **Decisión tuya:** vas a borrar la base actual y crear una nueva con las ramas adecuadas. Cuando lo hagas, hay que volver a aplicar las migraciones **001 a 011** y correr los seeders.
- Las pruebas de punta a punta `e2e_equipo.py` suponen una base limpia (códigos de empleado desde 0002 y sin equipos previos); con tus datos de prueba reales cinco de sus verificaciones fallan por eso.

## Diferidos por ti

- **6. Modo punto de venta / tienda en línea:** un interruptor en la barra superior para cambiar entre vistas. Espera a que apruebes el diseño.
- **7. Control de planes (plan gating):** "al final".
- **8 y 9. Gráficas del dashboard:** hoy son datos de ejemplo; pendientes de conectarse a datos reales.
- **10. Reporte CSV / PDF:** agregar propinas, descuentos y forma de pago.
- **12 a 19. Revisiones manuales:** cambio de contraseña y recuperación, correo opcional, sucursales o franquicias, geolocalización (regla acordada: toda acción permitida mientras sea dentro de la ubicación de confianza; ver `docs/Notas_Futuras.md`), prueba con cámara real, impresión e iPhone, y actualizar la documentación HTML y la presentación.
  - *Nota:* el resumen de la sesión no detalla el número de cada punto de este bloque. Confirmar antes de tomar uno.

## Otros pendientes técnicos

- **Archivos viejos que reaparecieron** (probablemente por OneDrive): `Kard/logging/` y, en el cliente, `src/app/auth/login/`, `src/app/auth/register/` y `src/app/auth/auth-shared.scss`. Son copias exactas de versiones que ya borraron commits anteriores. `logging/` tapa a la librería estándar de Python (rompe `python -m pytest` y el contenedor), y los del cliente rompen `ng build` y `ng test`. Se pueden borrar sin pérdida: están en el historial de git. Mientras estén, las pruebas se corren con `python -P` y apartándolos un momento.
- **Prueba con un iPhone y una cámara reales:** el escáner y el modo caja no se han probado en un iPhone real.
