# Ticket compartido entre la caja y el celular

Estado: **construido** (servidor y cliente), con pruebas. Este documento describe lo que quedó, no una propuesta.

## 1. Qué hace

Quien opera una caja registradora ve **el mismo ticket** en la computadora de la caja y en su celular. Lo que se escanea con el celular aparece en la computadora, como si fuera un escáner de mano, y lo que se borra en una pantalla se borra en la otra. Cualquiera de las dos puede cobrar.

- Si se va el internet, los cambios esperan en una fila y se reenvían al volver; el ticket no se pierde.
- Si se reinicia el servidor o Redis, el ticket se recupera de una copia en la base.
- Si el ticket se cobra en una pantalla, en la otra se vacía solo, y cobrarlo en las dos no lo duplica.

## 2. Cómo encuentra el celular su caja

No hay un QR extra. La caja es la que **la persona ya está operando**:

1. La persona entra a la caja: lee el QR de desbloqueo con su celular, o teclea su correo o código en la computadora. En ese momento el servidor anota en la caja quién la opera (`devices.operator_id`).
2. La computadora de la caja manda su token de equipo en cada petición, así que su caja es esa.
3. El celular no tiene token de equipo. Su caja es la que su dueño opera (`DeviceRepository.operated_by`).

Reglas:

- **Una persona opera una sola caja a la vez.** Si entra a otra, deja la primera.
- **Cuando entra otra persona a la caja, el ticket se vacía.** Nadie cobra lo que armó alguien más.
- **Al cerrar sesión en la caja** (`POST /device-auth/release`) queda sin operador y sin ticket.
- **Al cerrar la caja del día** se desautorizan las cajas y se borran todos los tickets en armado.
- Una persona que no opera ninguna caja usa Cobrar como siempre: su ticket se queda en su pantalla.

## 3. Dónde vive el ticket

| Dónde | Qué guarda | Para qué |
|---|---|---|
| **Redis** (`ticket:{negocio}:{caja}`) | El ticket vivo, con caducidad de 12 horas que **se renueva con cada cambio** | Lectura rápida (las pantallas preguntan cada segundo) |
| **Postgres** (`ticket_drafts`, migración 011) | Una copia en cada cambio | Recuperar el ticket si Redis se reinicia; es el almacén completo cuando no hay Redis |
| **Postgres** (`devices.operator_id`) | Quién opera cada caja | Que el celular encuentre su caja |

El ticket es una lista de líneas (producto y cantidad), un **identificador** (`uid`, cambia al cobrar o vaciar) y una **revisión** (`rev`, sube con cada cambio). El servidor **no guarda precios**: al cobrar vuelve a leerlos del catálogo.

La copia en la base solo avanza: una copia atrasada no pisa una más nueva. Si Redis falla a media operación, se sigue con la base.

## 4. Operaciones

Rutas (`/api/v1/tickets`, con el permiso de cobrar):

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/current` | El ticket de mi caja. Con `since` y `uid`, responde **204 sin cuerpo** si la pantalla ya tiene lo último |
| `POST` | `/ops` | Una operación: `add`, `set_quantity`, `remove`, `clear`. Devuelve el ticket completo |

Las operaciones están pensadas para **no chocar**:

- `add` suma (si las dos pantallas escanean el mismo producto son dos unidades, no una).
- `set_quantity` y `remove` apuntan a un producto; si ya no está, no pasa nada y no es un error.
- `clear` vacía y estrena identificador. Con `uid`, solo si sigue siendo ese ticket: un "vaciar" tardío de un ticket ya cobrado no borra el siguiente.
- Cada operación trae una llave (`op_id`); una repetida (reintento de red) no se aplica dos veces.
- Un ticket tiene como máximo 100 productos distintos y 999 unidades por producto.
- Solo se pueden agregar productos finales del negocio (404 si no).

Dos cambios simultáneos se conservan los dos: en Redis con `WATCH/MULTI`, y en la base con el candado de la fila (`SELECT ... FOR UPDATE`).

## 5. Cómo se mantienen al día las pantallas (cliente)

`TicketSyncService` (uno por pantalla de Cobrar):

- Pregunta cada **1.2 segundos**. Casi siempre contesta 204, que no pesa nada.
- Los cambios propios se ven al instante y se mandan **en orden, uno a la vez**. Si no hay internet, esperan en la fila y se reenvían con la **misma llave**: el servidor no los aplica dos veces. La lámpara pasa a "sin conexión (N por enviar)".
- Mientras haya cambios sin confirmar **no se pisa lo que se ve** con lo que dice el servidor.
- Una consulta lenta que salió antes de un cambio propio no regresa el ticket al pasado cuando por fin llega (`ResponseOrder`).
- Una sesión que no opera caja pregunta una vez y deja de preguntar.

La pantalla de Cobrar muestra una lámpara con el nombre de la caja: verde "sincronizada", o ámbar si no hay conexión.

## 6. Cobrar

La llave de idempotencia del cobro (`Idempotency-Key`) es el `uid` del ticket. Así, si la computadora y el celular cobran **el mismo ticket**, el servidor devuelve la misma venta la segunda vez (200) en vez de crear otra, y el inventario baja una sola vez. Después de cobrar se manda `clear` con ese `uid`, y la otra pantalla ve el ticket vacío y un aviso.

Sin caja, el ticket local también estrena una llave propia (`loc-…`) al cobrar, así que un doble clic tampoco cobra dos veces.

## 7. Seguridad

- **Aislamiento por negocio.** La clave de Redis lleva el negocio que sale del token. Una caja de otro negocio no existe para quien pregunta, aunque adivine su identificador o mande su token de equipo.
- **Solo quien opera la caja** puede soltarla; otra persona no puede vaciarle el ticket a quien está cobrando.
- Una caja **desautorizada o vencida** ya no se opera.
- Un identificador de producto inventado no entra al ticket.
- Las llaves de operación tienen forma fija (16 a 64 caracteres seguros): no entran saltos de línea ni caracteres raros.
- Cobrar **no depende del equipo**: cualquier puesto con permiso de cobrar lo hace desde su propio celular o computadora. Lo que sí sigue protegido es la pantalla de la caja registradora (autorizar y desbloquear).

## 8. Qué pasa cuando algo falla

| Falla | Resultado |
|---|---|
| Se va el internet en el celular | Sus cambios esperan en la fila y se reenvían al volver; el ticket de la caja sigue ahí |
| Se reinicia el servidor | El ticket sigue en Redis o en la base; las pantallas se reconectan solas |
| Redis se reinicia sin guardar datos | El ticket se recupera de la base y se vuelve a cargar |
| Redis cae por completo | Todo sigue con la base, sin Redis |
| Dos pantallas cambian lo mismo a la vez | Se conservan las dos operaciones |
| Se borra una línea en la computadora mientras el celular escanea otra | Se aplican las dos |
| Entra otra persona a la caja | El ticket se vacía y el celular de la anterior deja de ver la caja |
| La persona deja de operar la caja mientras tiene Cobrar abierto | La pantalla avisa y su ticket vuelve a ser solo de esa pantalla |
| Se cierra la caja del día | Los tickets en armado se borran |
| Un cobro falla y se reintenta | Misma llave: si sí se había guardado, no se duplica |

## 9. Lo que quedó fuera (a propósito)

- **Tiempo real por SSE o websocket.** Hoy se pregunta cada 1.2 s, que se siente casi inmediato y es robusto detrás de cualquier proxy. SSE queda como mejora si algún día importa bajar ese segundo.
- **Descuento, propina, forma de pago y nombre del cliente** no se comparten: solo las líneas del ticket. Cada pantalla captura lo suyo al cobrar.
- **Cajas múltiples con su propio turno de efectivo** (punto 5 de `docs/Pendientes.md`): hoy hay un solo turno por negocio. El ticket ya es por caja.
- **Límite de operaciones por segundo** por sesión.
- **Limpieza de filas vencidas** de `ticket_drafts`: hay como máximo una por caja y se borran al soltar o cerrar la caja.
- **Probado contra un Redis real:** el camino con Redis se probó con un Redis simulado que respeta `WATCH/MULTI`. Falta probarlo contra el Redis de Render.

## 10. Pruebas

Ver `docs/Pruebas_IEEE829.md`: PS-08 (servidor), PC-15, PC-16 y PC-17 (cliente). Además:

- `tests/integracion/e2e_ticket.py`: tablet, celular y laptop simulados contra la base real, con reversión.
- `tests/integracion/e2e_ticket_concurrencia.py`: cuatro conexiones a la vez sobre el mismo ticket; no se pierde ninguna operación.
