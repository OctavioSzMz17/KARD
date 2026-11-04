# Diseño: ticket sincronizado entre la caja y el celular

Estado: **propuesta para revisar**. No hay código. Depende de las cajas múltiples (punto 5 de `docs/Pendientes.md`), porque cada ticket pertenece a una caja.

## 1. El problema

Hoy el ticket vive en el navegador de quien lo arma. Si el empleado escanea productos con su celular, el ticket de la laptop (modo caja) no se entera. Tampoco hay forma de que, si se borra una línea en la laptop, el celular lo vea.

Lo que queremos:

- El celular funciona como **escáner** del ticket de una caja concreta.
- La laptop ve lo que escanea el celular casi al instante, y al revés.
- Si la conexión se va o el servidor se reinicia, el ticket sigue ahí un tiempo.
- Nada de esto puede abrir un hueco de seguridad (ver sección 6).

## 2. Idea central

El ticket en borrador vive **en el servidor**, no en cada navegador, y tiene un **número de revisión** que sube con cada cambio. Cada pantalla se limita a mostrar el último estado y a mandar cambios. Así, "borrar en la laptop" y "agregar en el celular" son lo mismo: cambios sobre un estado compartido.

```
celular ──POST op──►  servidor ──► Redis (ticket vivo + rev) ──► pub/sub ──► laptop (SSE)
laptop  ──POST op──►     │                                              └──► celular (SSE)
                         └──► Postgres (copia, para recuperar)
```

Las escrituras van por HTTP normal. Solo la lectura en tiempo real es un canal abierto: **SSE** (Server-Sent Events), de una sola vía. Un websocket no aporta nada aquí, porque el cliente no necesita hablarle al servidor por ese canal.

## 3. Modelo de datos

**Redis** (clave `ticket:{tenant_id}:{caja_id}`, un hash):

| Campo | Contenido |
|---|---|
| `rev` | Entero que sube con cada cambio |
| `uid` | Identificador del ticket; cambia al cobrar o vaciar. Sirve de `Idempotency-Key` del cobro |
| `lines` | Lista de `{line_id, product_id, quantity}` |
| `meta` | Descuento, propina, nombre del cliente, tipo de entrega, mesa |
| `ops` | Últimos identificadores de operación (para no aplicar dos veces la misma) |

- **TTL de 12 horas que se renueva con cada cambio.** Al cerrar la caja se borra.
- El servidor **nunca guarda precios**: al cobrar vuelve a leer los precios del catálogo (como hoy). Las líneas solo llevan producto y cantidad.

**Postgres** (migración `011_ticket_drafts.sql`, aún no escrita): tabla `ticket_drafts(caja_id PK, tenant_id, uid, rev, payload JSONB, updated_at)`.

- Se hace *upsert* en cada cambio, solo si `rev` es mayor que el guardado (así un guardado atrasado no pisa uno nuevo).
- Se lee de aquí solo si Redis no tiene el ticket (reinicio, expiración, caída). Entonces se vuelve a cargar en Redis.
- Se borra al cobrar, al vaciar, al cerrar la caja y, por limpieza, pasadas 24 horas.

## 4. Operaciones y sincronización

Endpoints (todos del negocio autenticado, con la caja resuelta desde el equipo o el emparejamiento):

| Método | Ruta | Para qué |
|---|---|---|
| `GET` | `/api/v1/tickets/current` | Estado actual completo (`rev`, `lines`, `meta`) |
| `POST` | `/api/v1/tickets/ops` | Aplicar una operación |
| `GET` | `/api/v1/tickets/stream` | Canal SSE con el estado nuevo |
| `POST` | `/api/v1/tickets/pairings` | Emparejar un celular (ver sección 5) |

Operaciones: `add_line` (suma cantidad si el producto ya está), `set_quantity`, `remove_line`, `set_meta`, `clear`. Cada una trae un `op_id` único.

- **Idempotencia.** Si el mismo `op_id` llega dos veces (reintento), el servidor no lo vuelve a aplicar y contesta con el estado actual. Se aplica en un script Lua en Redis, para que revisar el `op_id`, cambiar el ticket, subir `rev` y publicar sea **una sola operación atómica**.
- **Conflictos.** Las operaciones están pensadas para no chocar: `add_line` suma; `set_quantity` y `remove_line` apuntan a una línea concreta, y si esa línea ya no existe no hacen nada y devuelven el estado actual (no es un error). Solo `clear` puede llevar `base_rev`, y si no coincide da 409 para que no se borre un ticket que cambió mientras tanto.
- **Reconexión.** El canal SSE siempre manda el **estado completo** (no una lista de eventos), así que al reconectar basta con recibir el último. No hay que reconstruir historia.
- **Latidos.** El servidor manda un latido cada 15 segundos para que los proxies no corten la conexión.
- **Respaldo.** Si el canal falla, la pantalla pregunta cada 1 o 2 segundos `GET /tickets/current?since={rev}`; el servidor contesta 204 si no hay cambios.
- **La lámpara de "conectado"** es una clave de Redis con TTL de 30 segundos que renueva el canal del celular; la laptop la lee.

Al cobrar: el servidor usa el `uid` del ticket como `Idempotency-Key` del cobro (ver `docs/Pruebas_IEEE829.md`, PS-07), borra el ticket, genera un `uid` nuevo y avisa por el canal para que el celular quede vacío.

El ticket también se vacía cuando entra otra persona a la caja (por correo o código, por el QR de desbloqueo o al cerrar sesión), con el mismo aviso por el canal.

## 5. Emparejar el celular

Se reutiliza el modelo de los QR de autorización (ya probado):

1. La laptop muestra, bajo "Ticket", un QR de emparejamiento. El QR lleva solo un identificador aleatorio de un solo uso, en el fragmento de la dirección (no viaja al servidor).
2. El empleado lo lee con su celular, ya con su sesión iniciada, y confirma.
3. El servidor entrega **un permiso de emparejamiento** atado a esa caja y a esa persona. Caduca solo y se revoca al cerrar la caja o al bloquearla.
4. Ese permiso solo sirve para `add_line` y `set_quantity` en el ticket de esa caja, y para abrir el canal. **No sirve para cobrar.**

## 6. Seguridad

- **Aislamiento por negocio.** La clave de Redis lleva el `tenant_id` que sale del token, nunca del cuerpo. Una caja de otro negocio simplemente no existe para quien pregunta.
- **El permiso de emparejamiento solo escanea.** Cobrar no depende de ningún equipo (cualquier puesto cobra desde su propia sesión), pero el celular emparejado solo puede agregar y cambiar cantidades en el ticket de esa caja; el cobro se hace en la caja o en la propia sesión de la persona.
- **Sin datos sensibles en la dirección.** El canal SSE se abre con `fetch` y cabeceras, no con `EventSource`, para no poner tokens en la URL.
- **Límites.** Máximo de líneas por ticket, tope de cantidad por línea y límite de operaciones por segundo por emparejamiento, para que un celular roto o malicioso no inunde Redis.
- **Productos.** Cada `product_id` se valida contra el catálogo del negocio al aplicarse (o, como mínimo, al cobrar); uno inventado no llega a una venta.
- **Registro.** Emparejar, revocar y vaciar quedan en la bitácora, igual que autorizar un equipo.

## 7. Qué pasa cuando algo falla

| Falla | Resultado |
|---|---|
| Se cae la conexión del celular | La laptop sigue con su ticket; el celular reintenta y recibe el estado completo |
| Se reinicia el servidor | Redis y Postgres conservan el ticket; las pantallas se reconectan solas |
| Redis se reinicia sin persistencia | El ticket se recupera desde Postgres y se vuelve a cargar |
| Redis cae por completo | Se lee y escribe en Postgres; sin tiempo real, con el respaldo de consulta periódica |
| Dos pantallas cambian lo mismo a la vez | Gana el orden de llegada; ambas reciben el mismo estado final |
| Se borra una línea en la laptop mientras el celular agrega otra | Las dos operaciones se aplican; el celular ve el borrado |
| El empleado cierra la caja con un ticket abierto | El ticket se borra; el celular lo ve vacío |
| Otra persona entra a la caja | El ticket se vacía y el celular lo ve vacío |

## 8. Cosas del servidor a tener en cuenta

- Hoy los endpoints son `def` (síncronos) para no bloquear el servidor con la base. El canal SSE tiene que ser `async def` con el cliente asíncrono de Redis (`redis.asyncio`), o cada conexión abierta ocuparía un hilo del servidor y se repetiría el problema de rendimiento que ya arreglamos.
- Con **más de una instancia** en Render, el pub/sub de Redis es lo que avisa entre instancias. Con una sola funcionaría en memoria, pero conviene hacerlo con Redis desde el principio.
- **Redis en Render:** ya lo tienes (por eso funciona el carrito). Falta **confirmar su plan y si guarda datos en disco**; el plan gratuito de este tipo de servicios normalmente no persiste. Por eso la copia en Postgres no es opcional.

## 9. Plan de pruebas

- **Unitarias** (con Redis y base simulados): operaciones que no chocan, `op_id` repetido sin efecto, `rev` siempre creciente, `clear` con `base_rev` viejo da 409, límites de líneas y cantidad.
- **Contra Redis real:** el script Lua con dos clientes a la vez (sin `rev` repetidos, sin líneas perdidas), TTL que se renueva, recuperación desde Postgres con Redis vacío.
- **De punta a punta** (como `e2e_caja.py`): dos clientes simulados, uno borra y otro agrega; el canal entrega el estado final a los dos; reconexión tras cortar el canal.
- **Seguridad:** emparejamiento de otro negocio, permiso caducado o revocado, intento de cobrar con un permiso de emparejamiento (debe dar 403).
- **Mutaciones:** quitar la atomicidad del script, aceptar `op_id` repetido, no validar el negocio de la clave. La suite debe fallar cada vez.
- **Documentación:** casos nuevos en `docs/Pruebas_IEEE829.md`.

## 10. Orden de construcción

1. Cajas múltiples (punto 5): da el `caja_id` y separa los turnos.
2. Migración `011_ticket_drafts.sql` y el servicio de tickets con Redis y respaldo en Postgres.
3. Endpoints de operaciones y consulta, con pruebas.
4. Canal SSE y emparejamiento por QR.
5. Cliente: el ticket de Cobrar lee y escribe en el servidor; QR bajo "Ticket", lámpara de conexión y pantalla del celular solo para escanear.
6. Pruebas de punta a punta y documentación.

## 11. Decisiones

**Tomadas**

1. **El celular emparejado solo escanea, y se sincroniza con el modo caja.** No cobra por su permiso de emparejamiento.
2. **Cuando entra otra persona a la caja, el ticket se vacía.** Aplica al entrar con correo o código, al leer el QR de desbloqueo y al cerrar sesión en la caja. Así nadie cobra un ticket que armó alguien más.
3. **No existe bloquear la caja.** Se quitó la opción de todas las vistas y el bloqueo por inactividad: para cambiar de persona se cierra sesión (la caja queda lista para la siguiente).
4. **Cobrar no depende del equipo.** Cualquier puesto con permiso de cobrar lo hace desde su propio celular o computadora. El equipo autorizado solo existe para la pantalla de la caja registradora.

**Abiertas**

1. **¿Cuánto dura el ticket?** Propuesta: 12 horas renovables, o hasta cerrar la caja si ocurre antes.
2. **¿Cuántos celulares por caja?** Propuesta: varios, cada uno con su permiso.
3. **TTL del permiso de emparejamiento:** propuesta de 12 horas, igual que el equipo.
