# KARD — Especificación y Reporte de Pruebas (IEEE 829-2008)

**Proyecto:** KARD — Marketplace SaaS B2B2C
**Equipo:** Octavio Sánchez Mendoza · Brayan Adonai Díaz Santiago
**Fecha de ejecución:** 14 de agosto de 2026
**Versión del software:** backend `b6bd020` · frontend `2ff3a2b`

---

## 1. Plan de Pruebas (IEEE 829 — Test Plan)

### 1.1 Identificador
`KARD-TP-001`

### 1.2 Introducción
Este documento especifica, ejecuta y reporta seis casos de prueba —tres del
servidor y tres del cliente— sobre las reglas cuya falla comprometería el
producto completo. No busca cobertura exhaustiva, sino evidencia sobre los
puntos críticos.

### 1.3 Elementos a probar

| Elemento | Componente | Justificación |
|---|---|---|
| `ProductService` | Backend · capa de servicio | Concentra las reglas de catálogo e inventario |
| `validate_metadata` | Backend · validación JSONB | Frontera entre los dos giros de negocio |
| `AuthService` | Frontend · servicio | Custodia el token y distingue las dos identidades |
| `LoginComponent` | Frontend · componente | Puerta de entrada de ambos tipos de usuario |
| `authGuard` / `consumerGuard` | Frontend · guards | Separación de zonas en el enrutador |

### 1.4 Elementos que NO se prueban en esta iteración
Pasarela de pagos (Stripe, no implementada), envío real de correos (servicio
externo), y rendimiento bajo carga (fuera del alcance del MVP).

### 1.5 Enfoque
- **Servidor:** pruebas unitarias con `pytest` sobre la capa de servicio real,
  sustituyendo el repositorio por un doble en memoria. Esto es posible gracias
  al principio de **inversión de dependencias**: el servicio no conoce
  SQLAlchemy, así que se prueba sin levantar PostgreSQL.
- **Cliente:** pruebas unitarias con `Vitest` y `TestBed` de Angular,
  interceptando HTTP con `HttpTestingController` y la navegación con espías.

### 1.6 Criterio de aprobación
Un caso se aprueba cuando **todas** sus verificaciones pasan sin intervención
manual. Un solo fallo reprueba el caso completo.

### 1.7 Entorno

| | Servidor | Cliente |
|---|---|---|
| Runtime | Python 3.13 | Node 24 |
| Framework de pruebas | pytest 9.1.1 | Vitest 4.1.10 |
| Comando | `pytest tests/ -v` | `npm test` |
| Dependencias externas | ninguna | ninguna |

### 1.8 Responsabilidades
Diseño, ejecución y reporte: equipo de desarrollo.

---

## 2. Especificación de Casos de Prueba (Test Case Specification)

### PS-01 · Aislamiento multi-tenant

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-01` |
| **Elemento** | `ProductService.get_product` · `ProductService.sell_product` |
| **Requisito cubierto** | Rules.md §7 — aislamiento multi-tenant |
| **Prioridad** | Crítica |
| **Entradas** | Producto `p_secreto` perteneciente al negocio `t_aaa`; petición autenticada como negocio `t_bbb` |
| **Resultado esperado** | Se lanza `ProductNotFoundError` (HTTP 404). **No** `TenantAccessError` (403), porque un 403 confirmaría que el recurso existe |
| **Criterio de aprobación** | El negocio ajeno recibe 404; el dueño legítimo sí obtiene el producto; el `tenant_id` que llega al repositorio proviene del token, no de la petición |
| **Dependencias** | Ninguna |

### PS-02 · Validación del esquema JSONB

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-02` |
| **Elemento** | `products.models.validate_metadata` |
| **Requisito cubierto** | Rules.md §8 — enforcement de esquema JSONB |
| **Prioridad** | Alta |
| **Entradas** | Metadata de platillo válida; metadata sin campo obligatorio; metadata de óptica enviada a un restaurante; metadata con clave no declarada; giro `platform` |
| **Resultado esperado** | Los casos válidos pasan sin excepción; los cuatro inválidos lanzan `InvalidMetadataError` (HTTP 422) |
| **Criterio de aprobación** | Las seis verificaciones se comportan según lo esperado |
| **Dependencias** | Ninguna |

### PS-03 · El inventario nunca queda negativo

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-03` |
| **Elemento** | `ProductService.sell_product` |
| **Requisito cubierto** | Restricción `products_inventory_non_negative` |
| **Prioridad** | Crítica |
| **Entradas** | Producto con 3 unidades; intento de venta de 5. Además: venta de 4 sobre 10, y venta de 7 sobre 7 |
| **Resultado esperado** | La venta excesiva lanza `InsufficientStockError` (HTTP 400) sin modificar inventario, sin escribir bitácora y sin ejecutar commit. Las ventas válidas descuentan y registran el movimiento |
| **Criterio de aprobación** | Estado intacto tras el rechazo; en la venta válida, `cantidad_antes + cambio = cantidad_después` |
| **Dependencias** | Ninguna |

### PS-04 · Caja y cobro de mostrador

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-04` |
| **Elemento** | `CashService` · `OrderService.create_pos_sale` · `orders/money.py` · reglas de SKU y código de barras en `ProductService` |
| **Requisito cubierto** | Starter como punto de venta: cobrar solo con caja abierta, descuento, propina, cambio, corte con diferencia y búsqueda por código |
| **Prioridad** | Crítica (es dinero) |
| **Entradas** | Ventas en efectivo, tarjeta y transferencia con y sin descuento (porcentaje o monto) y propina; efectivo recibido de menos, exacto y de más; descuento mayor al subtotal; ventas canceladas dentro del turno; apertura doble; cierre con faltante, sobrante y exacto; SKU y código repetidos (también con otra capitalización) |
| **Resultado esperado** | Sin turno abierto cobrar falla con `CASH_CLOSED` y no mueve inventario. `total = subtotal − descuento + envío + propina` y el cambio es `recibido − total`; el redondeo es mitad hacia arriba. Un descuento mayor al subtotal o un pago insuficiente se rechazan **antes** de tocar el inventario. El efectivo esperado es `fondo + ventas en efectivo` (propina en efectivo incluida; canceladas y reembolsadas excluidas) y `diferencia = contado − esperado`. Un turno cerrado queda congelado. Solo puede haber un turno abierto por negocio. SKU y código de barras no se repiten dentro de un negocio. Lo que lee el escáner se resuelve por código de barras, SKU (sin distinguir mayúsculas) o id, solo dentro del negocio del token y sin productos eliminados |
| **Criterio de aprobación** | Las 80 verificaciones pasan, y cada regla falla si se la quita (se comprobó con tres mutaciones: sin el candado de caja cerrada, con el efectivo esperado calculado sobre el bruto, y con redondeo al par) |
| **Dependencias** | Migración `007_caja_punto_de_venta.sql` aplicada para la verificación contra base real |

### PS-05 · Equipo, puestos y permisos

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-05` |
| **Elemento** | `core/permissions.py` · `require_permission` · `TeamService` · todas las rutas del negocio |
| **Requisito cubierto** | Que cada persona del negocio haga solo lo que su puesto permite, y que nadie pueda saltarse la jerarquía |
| **Prioridad** | Crítica (seguridad) |
| **Entradas** | Los cinco puestos (dueño, gerente, subgerente, encargado de área, empleado); altas, cambios de puesto, bajas y cambios de contraseña de cada uno sobre cada otro; todas las rutas del negocio llamadas por un empleado y por un encargado; personas de otro negocio; códigos de empleado simultáneos; un puesto desconocido en la base |
| **Resultado esperado** | El empleado solo cobra, consulta el catálogo y ve si la caja está abierta. El encargado de área suma ajustar inventario. Gerente y subgerente supervisan pero no tocan suscripción ni personalización. El dueño puede todo. Nadie registra ni modifica a alguien de su nivel o superior, nadie se sube el puesto ni se da de baja a sí mismo, y el dueño no queda a merced de nadie. Un puesto desconocido cae al más bajo, no al más alto. Una persona de otro negocio responde 404 (no se confirma que exista). Los códigos de empleado son por negocio y los genera el sistema, también con altas simultáneas. La contraseña nunca llega a la bitácora. **Ninguna ruta del negocio queda sin guardia de permiso**, y cada una pide exactamente el acordado. Un cambio de puesto o una baja surten efecto de inmediato con el mismo token |
| **Criterio de aprobación** | Las 143 verificaciones pasan; las de punta a punta contra la base real también (se revierten solas). Cinco mutaciones de seguridad hacen fallar la suite: la jerarquía que deja tocar al par, una ruta de escritura sin guardia, un permiso de más al empleado, un puesto desconocido que cae al dueño y una consulta sin filtrar por negocio (esta última solo la detecta la prueba contra la base real) |
| **Dependencias** | Migración `008_equipo_y_puestos.sql` aplicada para la verificación contra base real |

### PS-06 · Equipos autorizados y acceso por QR

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-06` |
| **Elemento** | `DeviceService` · rutas `/device-auth` · `UserRepository.get_by_employee_code` |
| **Requisito cubierto** | Que una computadora solo pueda volverse caja registradora con la autorización de la gerencia, que ese acceso no se pueda robar fotografiando un QR y que cobrar NO dependa de ningún equipo autorizado |
| **Prioridad** | Crítica (seguridad) |
| **Entradas** | QR de autorización y de desbloqueo; aprobación por dueño, gerente, subgerente, encargado y empleado; códigos de 4 dígitos buenos y malos; QR caducados, ya usados, anulados y de otro negocio; reclamos con el secreto correcto y con uno ajeno; entrada con correo o código de empleado (propios, de otro negocio, de bajas, con contraseña mala); cobros de cada puesto sin equipo autorizado y con un token de equipo inventado; cierre de caja |
| **Resultado esperado** | Quien fotografía el QR no puede reclamar nada: el resultado solo se entrega a la computadora que guardó el secreto (el servidor solo conoce su hash) y una sola vez. Autorizar un equipo exige teclear el número de SU pantalla, con tres intentos; solo la gerencia autoriza. Un QR de otro negocio no existe. El token del equipo no queda en la base, solo su hash. Cualquier puesto con permiso de cobrar lo hace desde su propio celular o computadora, sin equipo autorizado: el equipo solo sirve para la pantalla de la caja registradora (consultar su estado sin token sigue dando 403 `DEVICE_NOT_AUTHORIZED`). El código de empleado se busca solo dentro del negocio del equipo. Al cerrar la caja todos los equipos del negocio quedan desautorizados. Las únicas rutas públicas son pedir un QR y recogerlo |
| **Criterio de aprobación** | Las 74 verificaciones pasan. De punta a punta contra la base real (`e2e_equipo.py`, se revierte sola), las de cobro sin equipo pasan; las que suponen una base limpia (códigos de empleado desde 0002, ningún equipo previo) fallan si la base ya tiene datos de prueba. Seis mutaciones de seguridad hacen fallar la suite: reclamo sin secreto, autorizar sin el número, QR de otro negocio visible, token guardado en claro, intentos de código sin límite y código de empleado buscado en todos los negocios (esta última solo la detecta la prueba contra la base real) |
| **Dependencias** | Migración `009_equipos_autorizados.sql` aplicada para la verificación contra base real |

### PS-07 · Cobro idempotente

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-07` |
| **Elemento** | `OrderService.create_pos_sale_idempotent` · `orders/idempotency.py` · `OrderRepository.lock_idempotency_key` · ruta `POST /orders/business/sales` |
| **Requisito cubierto** | Que un doble clic, un reintento tras perder la señal o una respuesta que no llegó no creen dos ventas ni descuenten dos veces el inventario |
| **Prioridad** | Crítica (integridad del dinero y del inventario) |
| **Entradas** | El mismo ticket enviado dos y tres veces con la misma llave; la misma llave con otra cantidad, otro importe o cobrando otra persona; la misma llave en otro negocio; un reintento después de cerrar la caja; llaves válidas e inválidas (largo, espacios, diagonales, saltos de línea); dos peticiones que compiten por la misma llave; cobros sin llave |
| **Resultado esperado** | La segunda vez devuelve la venta ya hecha (200 con `Idempotent-Replay: true`), sin crear otra y sin volver a descontar inventario. La misma llave con otro contenido o de otra persona da 409 `IDEMPOTENCY_CONFLICT`: nadie recibe el ticket de una venta ajena. Un reintento después de cerrar la caja devuelve la venta que sí se cobró, no "caja cerrada". La llave se reserva con un candado antes de buscar, y si dos peticiones llegan a competir por el índice único, la que pierde devuelve la venta de la ganadora; un choque ajeno a la llave no se disfraza de repetición. Una llave con mala forma da 422. Sin llave, cada cobro es una venta nueva (compatibilidad) |
| **Criterio de aprobación** | Las 51 verificaciones pasan. Diez mutaciones hacen fallar la suite (comprobado sobre una copia del proyecto): no reconocer el reintento, aceptar la misma llave con otro contenido, una huella que ignora a quien cobra, una huella que ignora propina y descuento, buscar antes de tomar el candado, revisar la caja antes del reintento, perder la carrera sin devolver la venta ganadora, disfrazar cualquier choque de la base como repetición, responder 201 a una repetición y aceptar llaves de cualquier forma |
| **Dependencias** | Migración `010_cobro_idempotente.sql` aplicada. **Pendiente:** el cliente aún no manda la llave, y falta una prueba de carrera real contra la base con dos conexiones |

### PS-08 · Ticket compartido entre la caja y el celular

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-08` |
| **Elemento** | `TicketService` · `tickets/logic.py` · `RedisDraftStore` · `PgDraftStore` · rutas `/tickets` · `DeviceRepository.operated_by` y `take_register` · `DeviceService.release_register` |
| **Requisito cubierto** | Que lo que se escanea con el celular aparezca en la computadora de la misma caja (y al revés), sin que dos pantallas se pisen, sin perder el ticket si falla Redis o el servidor, y sin que nadie vea o toque la caja de otro |
| **Prioridad** | Crítica (integridad del ticket y aislamiento entre negocios) |
| **Entradas** | Agregar, cambiar cantidad, quitar y vaciar; la misma operación repetida; dos cambios simultáneos al mismo ticket (dos pantallas, y cuatro conexiones reales a la base); un "vaciar" tardío de un ticket ya cobrado; un ticket en el máximo de productos; Redis vacío, caído o con error; una copia atrasada en la base; la computadora con su token de equipo, el celular de quien opera la caja, el celular de otra persona, el token de equipo de otro negocio, una caja desautorizada o vencida; entrar otra persona a la caja, cerrar sesión en ella y cerrar la caja del día; productos inventados, insumos y operaciones mal formadas |
| **Resultado esperado** | La computadora ve su caja por su token y el celular, por la caja que su dueño opera (sin QR extra). Una persona opera una sola caja a la vez. Agregar suma, y cambiar o quitar algo que ya no está no es un error; dos cambios simultáneos se conservan los dos (`WATCH/MULTI` en Redis, candado de la fila en la base). Una operación repetida no se aplica dos veces. Un "vaciar" con el identificador de otro ticket no borra el actual. El ticket vivo está en Redis con caducidad que se renueva con cada cambio, con copia en la base que solo avanza; si Redis se reinicia el ticket se recupera, y si cae todo sigue en la base. Cuando entra otra persona a la caja el ticket se vacía; al cerrar sesión en ella queda sin operador y sin ticket, y solo quien la opera puede soltarla; al cerrar la caja del día se borran los tickets. Una caja de otro negocio, desautorizada o vencida no se ve ni se toca. La consulta responde 204 sin cuerpo si la pantalla ya tiene lo último |
| **Criterio de aprobación** | Las 92 verificaciones pasan. Contra la base real: `e2e_ticket.py` (tablet, celular y laptop simulados; se revierte sola) y `e2e_ticket_concurrencia.py` (cuatro conexiones por doce operaciones sobre el mismo ticket: no se pierde ninguna; sin el candado de la fila se pierden 25 de 48). Veintidós mutaciones hacen fallar la suite o el recorrido contra la base real (comprobado sobre una copia del proyecto): agregar que reemplaza en vez de sumar, repetidas que se vuelven a aplicar, vaciar sin mirar de qué ticket, vaciar sin estrenar identificador, revisión que no sube, sin tope de productos, clave de Redis sin el negocio, guardar en Redis sin vigilar a otras peticiones, sin caducidad, sin copia en la base, sin recuperar de la base, un fallo de Redis que tumba el ticket, un equipo de otro negocio que cuenta como caja, agregar productos que el negocio no vende, no responder nunca 204, entrar otra persona sin vaciar el ticket, cualquiera soltando la caja de otro, una persona operando varias cajas, cajas desautorizadas que siguen operándose, cerrar la caja sin limpiar tickets, copia atrasada que pisa a la nueva y falta del candado de la fila |
| **Dependencias** | Migración `011_ticket_compartido.sql` aplicada. **Pendiente:** el camino con Redis se probó con un Redis simulado; falta probarlo contra el Redis de Render |

### PS-09 · Quién cobró, desde qué caja, el corte por cajero y el Dashboard real

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-09` |
| **Elemento** | `orders/cashier.py` · `OrderService` (cobro, reporte y estadísticas) · `orders/stats.py` · `OrderRepository.daily_sales` y `top_products` · `CashRepository.sales_aggregates` · `build_summary` · rutas `POST /orders/business/sales` y `GET /orders/business/stats` |
| **Requisito cubierto** | Que el ticket diga quién cobró y desde qué caja sin entregar datos personales; que las vistas internas sepan quién cobró cada venta; que el corte sume por cajero; y que el Dashboard muestre las ventas reales de los últimos días con los días del negocio, no los de Greenwich |
| **Prioridad** | Alta (dinero y reportes) |
| **Entradas** | Nombres con uno, dos o más apellidos, con acentos, con espacios de más y sin nombre; ventas desde la computadora de la caja, desde el celular de quien la opera, sin caja y reintentadas; ventas pagadas, canceladas y de otro negocio; turnos abiertos, cerrados y cortes anteriores que no traen la lista de cajeros; zonas horarias (UTC, México, al oriente de UTC) y ventas cerca de la medianoche; semanas con días sin ventas; días y desfases fuera de rango; un empleado pidiendo las estadísticas |
| **Resultado esperado** | El ticket lleva "Eva P." (el primer nombre y la inicial del último apellido; sin nombre, la parte local del correo; nunca el correo completo ni el apellido entero) y la caja "Caja 1". La venta guarda una **copia** del nombre de la caja de ese momento. Un reintento devuelve la caja de la venta original. El reporte trae nombre completo, código de empleado y caja; un pedido en línea no trae cajero. El corte suma cobrado y número de ventas por persona, de más a menos, y su total cuadra con el del turno; las ventas canceladas no cuentan; un corte cerrado conserva a los cajeros congelados. Las estadísticas cuentan solo ventas cobradas, por día del negocio (con el desfase del navegador), rellenan con ceros los días sin ventas, traen total, número de ventas y ticket promedio (sin promedio si no hubo ventas) y los 5 productos más vendidos. Cada negocio ve lo suyo; un empleado recibe 403 |
| **Criterio de aprobación** | Las 61 verificaciones pasan. Contra la base real: `e2e_panel.py` (se revierte sola; cobra desde la laptop, el celular y sin caja; revisa reporte, corte, una venta cancelada, estadísticas en dos zonas horarias, que las unidades coincidan con la base y el aislamiento entre negocios). Treinta y una mutaciones hacen fallar la suite o el recorrido contra la base real (comprobado sobre una copia del proyecto): 22 en las pruebas unitarias (apellido completo en el ticket, inicial del apellido equivocado, correo completo sin nombre, sin capitalizar, "hoy" sin el desfase, ventana a medianoche UTC, desfase con el signo cambiado, días vacíos omitidos, promedio mal dividido, servicio o ruta que ignoran el desfase, caja sin guardar o perdida en el reintento, reporte sin código o con correo, ticket con correo como cajero, ruta que acepta cualquier cantidad de días, corte sin cajeros o sin redondear) y 9 que solo ve la base real (día contado en UTC, ventas canceladas en las gráficas, en lo más vendido y en el corte, lo más vendido al revés, estadísticas sin filtrar por negocio, corte sin ordenar, reporte que pierde al cajero y celular que no encuentra su caja) |
| **Dependencias** | Migración `012_nombre_de_la_caja_en_la_venta.sql` aplicada para la verificación contra base real |

### PS-10 · Mensajes en español

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-10` |
| **Elemento** | Manejadores de error de `main.py` · `core/dependencies.py` · validaciones de los formularios y errores de los servicios |
| **Requisito cubierto** | Que ningún mensaje que llegue a la pantalla del usuario esté en inglés ("Product not found", "Invalid email or password", "Password must contain at least one digit"…) |
| **Prioridad** | Media (calidad de la interfaz) |
| **Entradas** | Cada mensaje de error de los manejadores, de sesión y permisos, de validación de contraseñas y nombres, y de los servicios de usuarios, carrito, pedidos y productos |
| **Resultado esperado** | Ninguno contiene palabras propias del inglés. El registro reconoce el correo repetido por su texto en español (y el cliente sigue entendiendo el del servidor anterior). Con la base real, la contraseña incorrecta dice "Correo o contraseña incorrectos", un producto inexistente "Producto no encontrado" y un correo repetido "ya está registrado" |
| **Criterio de aprobación** | Las 10 verificaciones pasan, más las 3 de `e2e_panel.py`. Dos mutaciones (volver a poner un mensaje de error y uno de sesión en inglés) hacen fallar la suite |
| **Dependencias** | Ninguna |

### PS-11 · Recuperar la contraseña por correo

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PS-11` |
| **Elemento** | `PasswordRecoveryService` · `RecoveryRepository` · rutas `POST /auth/forgot-password` y `POST /auth/reset-password` · `session_revoked` de `core/dependencies.py` · correos de `notifications/` |
| **Requisito cubierto** | Que cada persona (dueño, gerencia, empleado o cliente de la tienda) recupere su contraseña desde su correo sin molestar al resto del equipo, sin que eso abra una puerta: no revelar quién está registrado, enlace de un solo uso y con caducidad, sesiones anteriores cerradas |
| **Prioridad** | Alta (seguridad de acceso) |
| **Entradas** | Correos existentes, inexistentes, dados de baja, en mayúsculas y con espacios; los dos lados (negocio y cliente); varios enlaces seguidos; enlace vigente, usado, caducado, inventado, anulado por otro más nuevo y de una cuenta dada de baja; dos peticiones a la vez con el mismo enlace; contraseñas débiles y válidas; cuenta bloqueada por intentos fallidos; sesión abierta antes y después del cambio; nombre con HTML dentro; Redis caído |
| **Resultado esperado** | Pedir el enlace contesta **lo mismo** (202 y el mismo texto) exista o no el correo, y el correo se manda después de responder; nunca viaja el enlace en la respuesta. El enlace es aleatorio (256 bits), en la base solo queda su huella SHA-256, va en el fragmento `#` (no en la consulta), dura 1 hora y sirve una vez; pedir otro anula el anterior y los de una cuenta no tocan los de otra. Tope de 3 solicitudes por hora por correo y 20 por dirección, contado aunque la cuenta no exista; si Redis falla se deja pasar. Un enlace inexistente, caducado, usado o de una cuenta de baja da el mismo error 400 `INVALID_RESET_TOKEN`; gastarlo es un `UPDATE` atómico, así que dos peticiones simultáneas no cambian dos veces. La contraseña nueva sigue la regla del registro. Al cambiar: se quita el bloqueo por intentos fallidos, las sesiones anteriores dejan de valer (negocio y cliente) y la sesión del mismo segundo sobrevive, y se avisa por correo; el correo escapa el nombre y el enlace. Negocio y cliente son mundos separados |
| **Criterio de aprobación** | Las 66 verificaciones pasan. Contra la base real: `e2e_recuperar.py` (53 verificaciones, se revierte sola; empleado creado por el dueño y cliente de la tienda). Veintinueve mutaciones hacen fallar la suite o el recorrido contra la base real (comprobado sobre una copia): 23 en las pruebas unitarias (pedir otro sin anular el anterior, enlace sin gastar, cuenta de baja con enlace, enlace guardado en claro, 2 horas de vigencia, tope pasado por uno, sin tope por dirección, bloqueo sin quitar, contraseña sin guardar, correo sin normalizar, token en la consulta, correo sin mandar, aviso sin mandar, sesión del mismo segundo revocada, sesión vieja que sigue valiendo para usuario o cliente, token sin fecha de emisión, nombre o enlace sin escapar, contraseña débil aceptada, lado libre, entre otras) y 6 que solo ve la base real (el lado cliente busca en usuarios, gastar sin exigir "sin usar", gastar ignorando la caducidad, anular los enlaces de todas las cuentas, y no anotar la fecha del cambio de usuario o de cliente) |
| **Dependencias** | Migración `013_recuperar_contrasena.sql` aplicada para la verificación contra base real. En producción: `RESEND_API_KEY`, `EMAIL_FROM` y `APP_URL` configuradas en Render y un dominio de envío verificado en Resend; sin ellas el servidor responde igual pero no llega el correo |

### PC-01 · Custodia de sesión y separación de identidades

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-01` |
| **Elemento** | `AuthService` |
| **Requisito cubierto** | US-002 — contexto de tenant persistente vía token |
| **Prioridad** | Alta |
| **Entradas** | Respuesta de `/auth/login` (negocio) y de `/consumers/login` (cliente) |
| **Resultado esperado** | El token se persiste; la sesión de negocio expone tenant y rol; `getSession()` devuelve `null` ante una sesión de cliente; `logout()` limpia todo |
| **Criterio de aprobación** | Las cinco verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-02 · Pantalla única de acceso: interruptor Cliente / Negocio

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-02` |
| **Elemento** | `AuthPageComponent` (sirve `/login` y `/register`) |
| **Requisito cubierto** | US-001 — acceso con identidades separadas (B2B / B2C) y registro de ambos |
| **Prioridad** | Alta |
| **Entradas** | Formulario vacío o inválido; credenciales de cliente y de negocio en cada lado del interruptor; registro de cliente y de negocio; contraseñas distintas o débiles; correo ya registrado; errores 401 y 403 |
| **Resultado esperado** | Con formulario inválido no se emite ninguna petición. El interruptor decide la identidad: Cliente llama solo a `/consumers/login` y Negocio solo a `/auth/login`, sin reintentar con la otra (unas credenciales de negocio no entran como cliente). El registro de negocio usa `/auth/register-tenant` (no el endpoint del administrador de la plataforma) y el de cliente `/consumers/register`, y ambos dejan la sesión iniciada. Los errores del servidor se muestran en español. Al cambiar de lado cambian los campos exigidos y se limpia el error |
| **Criterio de aprobación** | Las diecinueve verificaciones pasan, incluida la ausencia de peticiones en los casos inválidos y de la llamada cruzada entre identidades |
| **Dependencias** | Ninguna |

### PC-03 · Guards de ruta

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-03` |
| **Elemento** | `authGuard` · `consumerGuard` |
| **Requisito cubierto** | Separación B2B/B2C reflejada en el enrutador |
| **Prioridad** | Alta |
| **Entradas** | Las tres sesiones posibles (negocio, cliente, anónima) contra los dos guards |
| **Resultado esperado** | `authGuard` deja pasar al negocio, desvía al cliente a `/explore` y al anónimo a `/login`. `consumerGuard` hace lo simétrico |
| **Criterio de aprobación** | Las seis combinaciones se comportan según lo esperado |
| **Dependencias** | Ninguna |

### PC-05 · Catálogo de negocios (explorar)

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-05` |
| **Elemento** | `ExploreComponent` |
| **Requisito cubierto** | Descubrimiento: el cliente encuentra un negocio y entra a su tienda |
| **Prioridad** | Media |
| **Entradas** | Listado con y sin banner/logo/eslogan; filtro por categoría y "Todos"; búsqueda escrita en ráfaga; error de red; cursor sobre una tarjeta con y sin "reducir movimiento" |
| **Resultado esperado** | Cada negocio es un enlace real a `/s/{slug}`. Un negocio sin banner usa el ícono de su giro y sin logo muestra su inicial. Elegir una categoría pide solo esa y "Todos" la quita. La búsqueda espera 300 ms y manda una sola petición con lo último escrito. Un fallo de carga se avisa. La tarjeta se inclina hacia el cursor salvo que el sistema pida reducir el movimiento |
| **Criterio de aprobación** | Las once verificaciones pasan; en particular, la de "reducir movimiento" falla si se quita su protección |
| **Dependencias** | Ninguna |

### PC-06 · Matemática del cobro (pantalla de punto de venta)

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-06` |
| **Elemento** | `pos-math.ts` (usado por `PosComponent`) |
| **Requisito cubierto** | La pantalla debe mostrar el mismo total y el mismo cambio que el servidor va a cobrar |
| **Prioridad** | Crítica |
| **Entradas** | Importes escritos de varias formas (`$1,200.00`, `.5`, `0.005`, vacío, texto); descuentos en porcentaje y en monto, válidos y fuera de rango; propinas válidas e inválidas; efectivo recibido de menos, exacto y de más; totales pequeños y grandes para los montos rápidos |
| **Resultado esperado** | Todo se calcula en centavos enteros (`0.1 + 0.2` da `0.30`). El medio centavo sube (`$0.005 → $0.01`). Un descuento mayor al subtotal es un error y no se recorta. El cambio es `recibido − total`; si no alcanza, dice cuánto falta. Los montos rápidos nunca ofrecen menos del total |
| **Criterio de aprobación** | Las veintiséis verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-07 · Lógica del escáner de cámara

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-07` |
| **Elemento** | `scan-logic.ts` (usado por `ScannerComponent` y `PosComponent`) |
| **Requisito cubierto** | Escanear productos con la cámara del celular sin cobrar de más ni de menos |
| **Prioridad** | Crítica (es dinero) |
| **Entradas** | Lecturas con espacios y saltos de línea, vacías y demasiado largas; el mismo código visto durante diez segundos seguidos; un código apartado y vuelto a apuntar; dos productos alternados; un parpadeo corto de la cámara; los formatos que declara un lector nativo |
| **Resultado esperado** | Un producto frente a la cámara cuenta una sola vez por muy a la vista que se quede. Cuenta otra vez solo si dejó de verse más de 1.2 s (apartarlo y volver a apuntarlo es "otro"). Un código distinto cuenta de inmediato. Un parpadeo de uno o dos cuadros no lo cuenta de nuevo. El lector nativo solo se usa si entiende QR, Code 128 y EAN-13; si no, se usa el de respaldo |
| **Criterio de aprobación** | Las trece verificaciones pasan, y la del producto que se queda a la vista falla si la ventana se renueva solo al contar (se comprobó con esa mutación) |
| **Dependencias** | Ninguna |

### PC-08 · Etiquetas de producto (QR y código de barras)

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-08` |
| **Elemento** | `labels.ts` (usado por la hoja de etiquetas de Productos) |
| **Requisito cubierto** | Imprimir etiquetas que el escáner pueda volver a leer |
| **Prioridad** | Alta |
| **Entradas** | Productos con código de barras y SKU, solo SKU, solo código de barras o ninguno; valores con letras, guiones y barras |
| **Resultado esperado** | La etiqueta lleva, en este orden, código de barras, SKU o el id del producto: el mismo orden con el que el servidor resuelve lo escaneado, así que lo impreso siempre encuentra su producto. El QR y las barras (Code 128) se generan como SVG que escala con la etiqueta y sin texto del usuario dentro. Valores distintos dan dibujos distintos |
| **Criterio de aprobación** | Las once verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-09 · Permisos en la pantalla

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-09` |
| **Elemento** | `AuthService.can` · `permissionGuard` · `permissions.ts` |
| **Requisito cubierto** | Que cada puesto vea en el menú solo lo suyo y no caiga en una pantalla en blanco al pedir una ajena (el servidor es quien protege; esto es comodidad y fidelidad) |
| **Prioridad** | Alta |
| **Entradas** | Sesiones de empleado, supervisor, dueño, anónima y de cliente; sesiones antiguas sin lista de permisos; una lista de permisos vacía; un cambio de perfil en caliente |
| **Resultado esperado** | Se usan los permisos que manda el servidor. Una sesión antigua de dueño conserva todo y una de personal queda solo con lo básico, nunca con más. Una lista vacía no cae al respaldo. Quien pide una pantalla que no le toca va a la primera que sí le corresponde (un empleado, a Cobrar), sin ciclos de redirección |
| **Criterio de aprobación** | Las dieciocho verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-10 · Contraseña inicial y datos del equipo

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-10` |
| **Elemento** | `team-logic.ts` (usado por la pantalla Equipo) |
| **Requisito cubierto** | Que la contraseña inicial que genera la pantalla sea válida para el servidor y fácil de leer o dictar |
| **Prioridad** | Media |
| **Entradas** | 500 contraseñas generadas; contraseñas cortas, sin número y sin letra; nombres de una o varias palabras, vacíos y con espacios de más |
| **Resultado esperado** | Toda contraseña generada cumple lo que exige el servidor (letra y número), tiene 12 caracteres, no usa caracteres que se confunden (0, O, 1, l, I) y no se repite. Las iniciales del avatar salen de las dos primeras palabras |
| **Criterio de aprobación** | Las once verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-11 · Acceso por QR y equipos autorizados (pantalla)

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-11` |
| **Elemento** | `device-logic.ts` |
| **Requisito cubierto** | Que el escáner del celular solo acepte QR de acceso de KARD, y que un equipo vencido no se use |
| **Prioridad** | Crítica (seguridad) |
| **Entradas** | QR de acceso, de un producto, de un SKU, de un sitio ajeno y con caracteres raros; fechas de vencimiento válidas, rotas y ausentes; registros del equipo dañados; el secreto y su hash |
| **Resultado esperado** | Solo se acepta la dirección de aprobación de KARD o un identificador con la forma exacta; cualquier otro código nunca se manda al servidor. El identificador va en el fragmento de la dirección, que no viaja al servidor. Un equipo vencido o con registro dañado no entrega token. El secreto son 32 bytes aleatorios y al servidor solo se le manda su SHA-256 |
| **Criterio de aprobación** | Las diecinueve verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-12 · Pantalla de la caja registradora

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-12` |
| **Elemento** | `RegisterModeComponent` |
| **Requisito cubierto** | Que la contraseña nunca viaje en la dirección. Un formulario sin controlar se envía solo por GET y deja `?password=…` en el historial y en los registros; ya ocurrió una vez durante el desarrollo |
| **Prioridad** | Crítica (seguridad) |
| **Entradas** | El envío del formulario de entrada; un equipo autorizado y uno sin autorizar; datos completos y vacíos |
| **Resultado esperado** | El envío nativo se frena, el formulario es de tipo POST, y la contraseña va en el cuerpo de la petición y nunca en la dirección. Sin datos no se llama al servidor. Un equipo sin autorizar muestra el QR y el número de verificación |
| **Criterio de aprobación** | Las seis verificaciones pasan, y tres de ellas fallan si se vuelve a un formulario sin controlar (se comprobó con esa mutación) |
| **Dependencias** | Ninguna |

### PC-13 · Entrar a Cobrar sin esperar autorización

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-13` |
| **Elemento** | `PosComponent` · rutas del panel · `AuthService.homeRoute` |
| **Requisito cubierto** | Que quien inicia sesión con el login normal entre a su panel y cobre, sin que nadie autorice su equipo: el equipo autorizado solo existe para la pantalla de la caja registradora |
| **Prioridad** | Alta (flujo de trabajo del personal) |
| **Entradas** | Los cinco puestos con inicio de sesión normal, con y sin equipo guardado en el navegador |
| **Resultado esperado** | A nadie lo mandan a `/caja-registradora` ni a una pantalla de autorización, y nadie ve un aviso de equipo no autorizado. La ruta de Cobrar exige solo el permiso, sin guardia de equipo |
| **Criterio de aprobación** | Las nueve verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-14 · Menú y salida del panel

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-14` |
| **Elemento** | `DashboardLayoutComponent` |
| **Requisito cubierto** | Que cada puesto vea solo sus ventanas, que no exista la opción de bloquear la caja y que salir de una caja registradora la deje lista para la siguiente persona |
| **Prioridad** | Alta |
| **Entradas** | Empleado y gerencia, con y sin caja autorizada en el navegador; cerrar sesión en cada caso |
| **Resultado esperado** | El empleado ve Cobrar, Escanear y Productos, y nada de gestión; la gerencia ve además el panel y la gestión. No existe "Bloquear caja" ni el botón correspondiente, esté o no autorizada la caja. En una caja autorizada, "Cerrar sesión" primero avisa al servidor que la caja quedó libre (sin operador y con el ticket vacío) y después deja la caja en su pantalla de entrada; si el servidor falla o no hay conexión, a los 3 segundos cierra igual y una sola vez. Fuera de una caja, vuelve al inicio de sesión |
| **Criterio de aprobación** | Las quince verificaciones pasan (cuatro de ellas, del menú en español y sin opciones que no existen). Dos mutaciones hacen fallar la suite (comprobado sobre una copia): cerrar sesión sin soltar la caja en el servidor y esperar al servidor sin tope de tiempo; y dos más del menú: volver a ofrecer 'Customers' o volver a escribir 'Products' |
| **Dependencias** | Ninguna |

### PC-15 · Lógica del ticket compartido

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-15` |
| **Elemento** | `ticket-sync-logic.ts` |
| **Requisito cubierto** | Que las operaciones del ticket tengan la forma que acepta el servidor, que las líneas del servidor se muestren con su producto completo y que una respuesta vieja nunca regrese el ticket al pasado |
| **Prioridad** | Alta |
| **Entradas** | Agregar, cambiar cantidad (negativa, cero, enorme), quitar y vaciar con y sin identificador; llaves de operación y de ticket; líneas con productos que ya no están en el catálogo o con el catálogo sin cargar; listas en otro orden; respuestas que llegan desordenadas |
| **Resultado esperado** | Las llaves tienen la forma segura que acepta el servidor y nunca se repiten. La cantidad nunca sale de 1 a 999. Las líneas conservan el orden del servidor y un producto desconocido se omite sin romper nada. Una respuesta solo vale si salió después de la última aplicada |
| **Criterio de aprobación** | Las veinticinco verificaciones pasan |
| **Dependencias** | Ninguna |

### PC-16 · Servicio de sincronización del ticket

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-16` |
| **Elemento** | `TicketSyncService` |
| **Requisito cubierto** | Que lo que se escanea en el celular aparezca en la computadora de la misma caja y al revés, y que no se pierda nada si se va el internet |
| **Prioridad** | Crítica |
| **Entradas** | Arranque con y sin caja; consultas con 200, 204 y error; cambios en orden, uno a la vez; un corte de internet y su regreso; errores 500, 404, 409 `TICKET_FULL` y 409 `NO_REGISTER`; respuestas que llegan desordenadas; cambios enviados antes de saber si hay caja |
| **Resultado esperado** | Una sesión sin caja pregunta una vez y deja de preguntar. Con caja pregunta cada 1.2 s con la revisión y el ticket que ya tiene. Los cambios salen en orden; si no hay conexión esperan en la fila y se reenvían con la MISMA llave, y la lámpara avisa. Mientras haya cambios sin confirmar no se pisa lo que se ve. Un rechazo del servidor descarta ese cambio, avisa y vuelve a pedir el ticket completo; si ya no opera la caja, deja de sincronizar |
| **Criterio de aprobación** | Las veintiocho verificaciones pasan. Seis mutaciones hacen fallar la suite (comprobado sobre una copia): pisar lo que se ve con cambios sin enviar, descartar el cambio al fallar la red, aplicar una consulta vieja, mandar cambios sin caja, seguir preguntando sin caja y mandar los cambios todos a la vez. Una séptima, quitar la guarda de orden al confirmar un cambio propio, no cambia nada observable (hoy no se consulta mientras hay cambios sin confirmar), así que es una defensa y no una regla que se pueda probar |
| **Dependencias** | Ninguna |

### PC-17 · Cobrar con el ticket compartido

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-17` |
| **Elemento** | `PosComponent` · `SalesService.charge` |
| **Requisito cubierto** | Que el ticket de Cobrar sea el mismo en la computadora y en el celular de la caja, y que cobrarlo en las dos no lo duplique |
| **Prioridad** | Crítica (dinero e inventario) |
| **Entradas** | Abrir Cobrar con y sin caja; lo que llega del otro dispositivo (agregado, vaciado o cobrado); agregar, subir, bajar, quitar y vaciar aquí; escanear con la cámara; cobrar, fallar y reintentar; cobrar un ticket ya cobrado en el otro dispositivo |
| **Resultado esperado** | Lo que dice el servidor se ve en pantalla; si el otro dispositivo vació o cobró el ticket, aquí queda vacío y se avisa. Cada cambio se ve al instante y se manda al servidor (subir la cantidad manda la cantidad nueva, no "sumar uno"). Sin caja no se manda nada. El cobro lleva el identificador del ticket como `Idempotency-Key`; un reintento usa la misma llave y el siguiente ticket estrena otra. Tras cobrar se vacía el ticket compartido solo si sigue siendo ese. La lámpara muestra la caja y si está sincronizada |
| **Criterio de aprobación** | Las dieciocho verificaciones pasan. Seis mutaciones hacen fallar la suite (comprobado sobre una copia): cobrar sin llave, usar otra llave distinta de la del ticket, no mandar lo que se agrega, vaciar cualquier ticket, reutilizar la llave local y mandar "agregar uno" en vez de la cantidad |
| **Dependencias** | Ninguna |

### PC-18 · Cuentas de las gráficas del Dashboard

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-18` |
| **Elemento** | `chart-math.ts` |
| **Requisito cubierto** | Que las gráficas se dibujen con lo que de verdad vendió el negocio, que un día sin ventas sea un dato (el piso) y que los nombres de los días sean los del negocio |
| **Prioridad** | Alta |
| **Entradas** | Series con ceros, con un solo día de ventas, proporcionales, con importes negativos, vacías y de un solo dato; fechas de una semana, de un solo día y que cruzan fin de mes y de año; cantidades vendidas de lo más vendido |
| **Resultado esperado** | El valor más alto queda arriba (con margen) y el cero abajo, repartido parejo de borde a borde. Sin ventas es una línea plana en el piso, no una curva inventada. El resultado nunca cambia entre una llamada y otra. Los días salen del texto de la fecha sin correrse por la zona horaria y el último dice "Hoy". Una barra con algo vendido nunca desaparece |
| **Criterio de aprobación** | Las veinte verificaciones pasan. Cinco mutaciones hacen fallar la suite (comprobado sobre una copia): la gráfica al revés, la línea sin ventas a media altura, el último día sin "Hoy", las barras que desaparecen y las barras con la proporción invertida |
| **Dependencias** | Ninguna |

### PC-19 · Dashboard con ventas reales

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-19` |
| **Elemento** | `HomeComponent` |
| **Requisito cubierto** | Que el inicio muestre lo que el servidor dice que se vendió (y no números aleatorios), con los días del negocio, y qué se ve sin ventas, mientras carga, con un error o sin permiso |
| **Prioridad** | Alta |
| **Entradas** | Respuestas con y sin ventas; una venta (singular); error del servidor y reintento; sesiones con y sin permiso de ver ventas; entrar dos veces con los mismos datos |
| **Resultado esperado** | Pide 7 días con el desfase del navegador y la sesión del negocio. Muestra el total, cuántas ventas (singular o plural) y el ticket promedio. La línea es exactamente la de los importes de cada día, con las etiquetas de los días del negocio. Al volver a entrar la gráfica es idéntica. Lo más vendido se lista con sus unidades y barras proporcionales. Sin ventas: línea plana y un mensaje; sin ticket promedio inventado. Mientras llega dice "Cargando"; si falla lo avisa y deja reintentar. Quien no puede ver ventas no las pide ni ve esas tarjetas. Ya no existen la tendencia de stock ni el trazo decorativo inventados |
| **Criterio de aprobación** | Las diecisiete verificaciones pasan. Seis mutaciones hacen fallar la suite (comprobado sobre una copia): pedir ventas sin permiso, pedir 30 días, dibujar cuántas ventas en vez de cuánto dinero, decir siempre que hay ventas, no avisar un error de carga y volver el trazo inventado |
| **Dependencias** | Ninguna |

### PC-20 · Quién cobró y desde qué caja (ticket, ventas y corte)

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-20` |
| **Elemento** | `ReceiptComponent` · `SalesComponent` · `CutSheetComponent` · `CashComponent` |
| **Requisito cubierto** | Que el ticket del cliente diga la caja y quién atendió con nombre e inicial, y que las vistas internas muestren el nombre completo y el código |
| **Prioridad** | Alta |
| **Entradas** | Tickets con y sin caja y con y sin cajero; un reporte con ventas de mostrador y pedidos en línea; la descarga CSV; el corte con y sin lista de cajeros, vacío, y el turno abierto |
| **Resultado esperado** | El ticket dice "Caja: Caja 1" antes que "Atendió: Eva P."; sin caja o sin cajero omite esa línea; nunca muestra un correo ni el nombre completo. El reporte tiene la columna Cajero (nombre, código y caja) justo después de Cliente, y una raya en pedidos en línea. El CSV agrega Cajero y Caja, con las celdas vacías (no "null") en pedidos en línea. El corte, en pantalla y en la hoja impresa, lista a cada cajero; un corte anterior sin la lista se lee igual |
| **Criterio de aprobación** | Las diecisiete verificaciones pasan. Ocho mutaciones hacen fallar la suite (comprobado sobre una copia): el ticket sin la caja, con la caja después o sin quien atendió, la hoja y la pantalla del corte sin cajeros, el reporte sin código o sin caja y el CSV sin cajero |
| **Dependencias** | Ninguna |

### PC-21 · Servicio de ventas (estadísticas y llave del cobro)

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-21` |
| **Elemento** | `SalesService` |
| **Requisito cubierto** | Que las estadísticas se pidan con la zona horaria del navegador y que el cobro mande la llave de idempotencia solo si hay una |
| **Prioridad** | Alta |
| **Entradas** | Estadísticas por defecto y de otro número de días; un cobro con y sin llave |
| **Resultado esperado** | Pide 7 días con `tz_offset` igual al desfase real del navegador y con la sesión del negocio, sin mandar el negocio (sale del token). El cobro lleva `Idempotency-Key` solo cuando recibe una |
| **Criterio de aprobación** | Las seis verificaciones pasan. Una mutación hace fallar la suite (comprobado sobre una copia): pedir las estadísticas sin el desfase de la zona horaria |
| **Dependencias** | Ninguna |

### PC-22 · Pantalla de recuperar la contraseña

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-22` |
| **Elemento** | `PasswordRecoveryComponent` · `recovery-logic` · `AuthService.forgotPassword/resetPassword` · enlace "¿Olvidaste tu contraseña?" del inicio de sesión |
| **Requisito cubierto** | Pedir el enlace y crear la contraseña nueva desde el celular o la computadora, sin delatar si un correo está registrado |
| **Prioridad** | Alta |
| **Entradas** | Correos válidos, inválidos y con espacios al final; los dos lados; direcciones con el token completo, cortado, con caracteres raros o ausente; contraseñas débiles, que no coinciden y válidas; respuestas 202, 200, 400 `INVALID_RESET_TOKEN`, 500 y sin conexión |
| **Resultado esperado** | Sin correo válido no se manda nada; se recorta el espacio que dejan los teclados del celular. El lado sale de `?tipo=`, luego de lo último usado en el login, y por último cliente. Tras pedirlo siempre dice "Revisa tu correo". El token se lee del `#`; si falta o no tiene forma de token no hay formulario y se ofrece pedir otro. Una contraseña débil o que no coincide no viaja. Un enlace que el servidor rechaza manda a pedir otro en vez de dejar reintentar. Al terminar **no** inicia sesión sola. El token no se guarda en ningún almacenamiento. En el login el enlace lleva a `/recuperar` con el lado elegido y no aparece en el registro |
| **Criterio de aprobación** | Las 27 verificaciones pasan. Quince mutaciones hacen fallar la suite (comprobado sobre una copia): token sin validar, token corto, mensajes, contraseñas que no coinciden o débiles, enlace rechazado que deja reintentar, espacios sin recortar, lado fijo, sin aviso de envío, sin token mostrando el formulario, lado olvidado, destino final equivocado, petición sin token o sin lado y enlace del login que pierde el lado. También se revisó la pantalla en el navegador (computadora y celular) con respuestas simuladas del servidor |
| **Dependencias** | Ninguna |

### PC-23 · Rutas de recuperar la contraseña

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-23` |
| **Elemento** | `app.routes.ts` |
| **Requisito cubierto** | Que `/recuperar` y `/restablecer` abran la pantalla en su modo y no pidan sesión |
| **Prioridad** | Media |
| **Entradas** | La tabla de rutas |
| **Resultado esperado** | `/recuperar` → modo "pedir el enlace"; `/restablecer` → modo "contraseña nueva"; ninguna lleva guardia; las dos cargan el mismo componente |
| **Criterio de aprobación** | Las 4 verificaciones pasan. Una mutación (ruta `/restablecer` en el modo de pedir) hace fallar la suite |
| **Dependencias** | Ninguna |

---

## 3. Procedimiento de Pruebas (Test Procedure)

### 3.1 Servidor
```bash
cd Kard
pip install -r requirements.txt
pytest tests/ -v
```

### 3.2 Cliente
```bash
cd Kard-client
npm install
npm test
```

Ninguno de los dos procedimientos requiere base de datos, Redis ni Docker: los
dobles de prueba sustituyen toda dependencia externa. Eso los vuelve
reproducibles en cualquier máquina y aptos para integración continua.

---

## 4. Reporte de Ejecución (Test Log + Test Summary Report)

### 4.1 Bitácora

| Caso | Verificaciones | Resultado | Duración |
|---|:--:|:--:|---|
| PS-01 · Aislamiento multi-tenant | 3 | ✅ Aprobado | 1.01 s (suite) |
| PS-02 · Validación JSONB | 6 | ✅ Aprobado | — |
| PS-03 · Inventario no negativo | 5 | ✅ Aprobado | — |
| PS-04 · Caja y cobro de mostrador | 80 | ✅ Aprobado | — |
| PS-05 · Equipo, puestos y permisos | 143 | ✅ Aprobado | — |
| PS-06 · Equipos autorizados y acceso por QR | 74 | ✅ Aprobado | — |
| PS-07 · Cobro idempotente | 51 | ✅ Aprobado | — |
| PS-08 · Ticket compartido entre la caja y el celular | 92 | ✅ Aprobado | — |
| PS-09 · Quién cobró, caja, corte por cajero y Dashboard real | 61 | ✅ Aprobado | — |
| PS-10 · Mensajes en español | 10 | ✅ Aprobado | — |
| PS-11 · Recuperar la contraseña por correo | 66 | ✅ Aprobado | — |
| PC-01 · AuthService | 7 | ✅ Aprobado | — |
| PC-02 · AuthPageComponent | 20 | ✅ Aprobado | — |
| PC-03 · Guards de ruta | 6 | ✅ Aprobado | — |
| PC-05 · ExploreComponent | 11 | ✅ Aprobado | — |
| PC-06 · Matemática del cobro | 26 | ✅ Aprobado | — |
| PC-07 · Lógica del escáner | 13 | ✅ Aprobado | — |
| PC-08 · Etiquetas de producto | 11 | ✅ Aprobado | — |
| PC-09 · Permisos en la pantalla | 18 | ✅ Aprobado | — |
| PC-10 · Contraseña inicial y datos del equipo | 11 | ✅ Aprobado | — |
| PC-11 · Acceso por QR y equipos autorizados | 19 | ✅ Aprobado | — |
| PC-12 · Pantalla de la caja registradora | 6 | ✅ Aprobado | — |
| PC-13 · Entrar a Cobrar sin esperar autorización | 9 | ✅ Aprobado | — |
| PC-14 · Menú y salida del panel | 15 | ✅ Aprobado | — |
| PC-15 · Lógica del ticket compartido | 25 | ✅ Aprobado | — |
| PC-16 · Servicio de sincronización del ticket | 28 | ✅ Aprobado | — |
| PC-17 · Cobrar con el ticket compartido | 18 | ✅ Aprobado | — |
| PC-18 · Cuentas de las gráficas del Dashboard | 20 | ✅ Aprobado | — |
| PC-19 · Dashboard con ventas reales | 17 | ✅ Aprobado | — |
| PC-20 · Quién cobró y desde qué caja | 17 | ✅ Aprobado | — |
| PC-21 · Servicio de ventas | 6 | ✅ Aprobado | — |
| PC-22 · Pantalla de recuperar la contraseña | 27 | ✅ Aprobado | — |
| PC-23 · Rutas de recuperar la contraseña | 4 | ✅ Aprobado | — |
| **TOTAL** | **921** | **33 de 33 aprobados** | |

### 4.2 Salida del servidor
```
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.1.1
collected 14 items

tests/test_products.py::TestAislamientoMultiTenant  ...................  3 PASSED
tests/test_products.py::TestValidacionMetadataJSONB ...................  6 PASSED
tests/test_products.py::TestStockNoNegativo         ...................  5 PASSED

============================= 14 passed in 1.01s ==============================
```

### 4.3 Salida del cliente
```
 RUN  v4.1.10  Kard-client

 Test Files  3 passed (3)
      Tests  17 passed (17)
   Duration  4.16s
```

### 4.4 Incidencias detectadas y resueltas durante la ejecución

| # | Descripción | Resolución |
|---|---|---|
| 1 | El proyecto Angular no tenía runner de pruebas configurado | Se instaló Vitest y se agregó el target `test` en `angular.json` |
| 2 | Tres rechazos no manejados: los componentes navegan tras el login y el enrutador de prueba no tiene rutas | Se interceptó `Router.navigate` con espías en las suites afectadas |

### 4.5 Evaluación

Los seis casos aprobaron. La cobertura se concentró deliberadamente en las
reglas que sostienen la promesa comercial del producto: que un negocio jamás
vea datos de otro (PS-01, PC-03), que el catálogo respete el esquema de su
giro (PS-02) y que el inventario nunca quede en un estado imposible (PS-03).

**Riesgo residual:** las pruebas del servidor sustituyen el repositorio, por lo
que verifican que el servicio *pida* siempre el filtro por tenant, no que
PostgreSQL lo aplique. Una prueba de integración contra una base real cerraría
esa brecha; queda propuesta para la siguiente iteración.

---

## 5. Archivos de prueba

| Caso | Archivo |
|---|---|
| PS-01, PS-02, PS-03 | `Kard/tests/test_products.py` |
| PS-04 | `Kard/tests/test_caja_pos.py` |
| PS-04 contra base real (38 verificaciones, se revierten solas) | `Kard/tests/integracion/e2e_caja.py` |
| PS-05 | `Kard/tests/test_equipo_permisos.py` |
| PS-05 y PS-06 de punta a punta contra la base real (se revierten solas; suponen una base limpia) | `Kard/tests/integracion/e2e_equipo.py` |
| PS-06 | `Kard/tests/test_dispositivos.py` |
| PS-07 | `Kard/tests/test_cobro_idempotente.py` |
| PS-08 | `Kard/tests/test_ticket_compartido.py` |
| PS-08 de punta a punta contra la base real (se revierte sola) | `Kard/tests/integracion/e2e_ticket.py` |
| PS-08, concurrencia real con cuatro conexiones (crea y borra una caja temporal) | `Kard/tests/integracion/e2e_ticket_concurrencia.py` |
| PS-09 y PS-10 | `Kard/tests/test_panel_y_cajero.py` |
| PS-09 y PS-10 de punta a punta contra la base real (se revierte sola) | `Kard/tests/integracion/e2e_panel.py` |
| PS-11 | `Kard/tests/test_recuperar_contrasena.py` |
| PS-11 de punta a punta contra la base real (se revierte sola) | `Kard/tests/integracion/e2e_recuperar.py` |
| Dobles y fixtures | `Kard/tests/conftest.py` |
| PC-01 | `Kard-client/src/app/core/services/auth.service.spec.ts` |
| PC-02 | `Kard-client/src/app/auth/auth-page/auth-page.component.spec.ts` |
| PC-03 | `Kard-client/src/app/core/guards/auth.guard.spec.ts` |
| PC-06 | `Kard-client/src/app/dashboard/pos/pos-math.spec.ts` |
| PC-07 | `Kard-client/src/app/dashboard/pos/scan-logic.spec.ts` |
| PC-08 | `Kard-client/src/app/dashboard/products/labels.spec.ts` |
| PC-09 | `Kard-client/src/app/core/guards/permission.guard.spec.ts` |
| PC-10 | `Kard-client/src/app/dashboard/team/team-logic.spec.ts` |
| PC-11 | `Kard-client/src/app/core/device-logic.spec.ts` |
| PC-12 | `Kard-client/src/app/register-mode/register-mode.component.spec.ts` |
| PC-13 | `Kard-client/src/app/dashboard/pos/pos.component.spec.ts` |
| PC-14 | `Kard-client/src/app/dashboard/layout/dashboard-layout.component.spec.ts` |
| PC-15 | `Kard-client/src/app/core/ticket-sync-logic.spec.ts` |
| PC-16 | `Kard-client/src/app/core/services/ticket-sync.service.spec.ts` |
| PC-17 | `Kard-client/src/app/dashboard/pos/pos.component.spec.ts` (segunda parte del archivo) |
| PC-18 | `Kard-client/src/app/dashboard/home/chart-math.spec.ts` |
| PC-19 | `Kard-client/src/app/dashboard/home/home.component.spec.ts` |
| PC-20 | `Kard-client/src/app/dashboard/pos/receipt.component.spec.ts` |
| PC-21 | `Kard-client/src/app/core/services/sales.service.spec.ts` |
| PC-22 | `Kard-client/src/app/auth/password-recovery/password-recovery.component.spec.ts` (más `auth.service.spec.ts` y `auth-page.component.spec.ts`) |
| PC-23 | `Kard-client/src/app/app.routes.spec.ts` |
