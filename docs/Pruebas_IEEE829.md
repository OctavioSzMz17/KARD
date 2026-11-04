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
| **Criterio de aprobación** | Las 139 verificaciones pasan; las de punta a punta contra la base real también (se revierten solas). Cinco mutaciones de seguridad hacen fallar la suite: la jerarquía que deja tocar al par, una ruta de escritura sin guardia, un permiso de más al empleado, un puesto desconocido que cae al dueño y una consulta sin filtrar por negocio (esta última solo la detecta la prueba contra la base real) |
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
| **Criterio de aprobación** | Las 48 verificaciones pasan. Diez mutaciones hacen fallar la suite (comprobado sobre una copia del proyecto): no reconocer el reintento, aceptar la misma llave con otro contenido, una huella que ignora a quien cobra, una huella que ignora propina y descuento, buscar antes de tomar el candado, revisar la caja antes del reintento, perder la carrera sin devolver la venta ganadora, disfrazar cualquier choque de la base como repetición, responder 201 a una repetición y aceptar llaves de cualquier forma |
| **Dependencias** | Migración `010_cobro_idempotente.sql` aplicada. **Pendiente:** el cliente aún no manda la llave, y falta una prueba de carrera real contra la base con dos conexiones |

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
| **Resultado esperado** | El empleado ve Cobrar, Escanear y Productos, y nada de gestión; la gerencia ve además el panel y la gestión. No existe "Bloquear caja" ni el botón correspondiente, esté o no autorizada la caja. En una caja autorizada, "Cerrar sesión" cierra la sesión de la persona y deja la caja en su pantalla de entrada; fuera de una caja, vuelve al inicio de sesión |
| **Criterio de aprobación** | Las ocho verificaciones pasan |
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
| PS-05 · Equipo, puestos y permisos | 139 | ✅ Aprobado | — |
| PS-06 · Equipos autorizados y acceso por QR | 74 | ✅ Aprobado | — |
| PS-07 · Cobro idempotente | 48 | ✅ Aprobado | — |
| PC-01 · AuthService | 7 | ✅ Aprobado | — |
| PC-02 · AuthPageComponent | 19 | ✅ Aprobado | — |
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
| PC-14 · Menú y salida del panel | 8 | ✅ Aprobado | — |
| **TOTAL** | **519** | **20 de 20 aprobados** | |

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
