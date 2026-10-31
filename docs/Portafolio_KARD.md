# KARD — Resumen de proyecto y método de trabajo

**Autor:** Octavio Sánchez Mendoza
**Periodo:** 7 de junio – 21 de agosto de 2026 (~11 semanas)
**Rol:** arquitecto y director técnico · desarrollo asistido por IA
**Repositorios:** [KARD](https://github.com/OctavioSzMz17/KARD) (backend) · [KARD-Client](https://github.com/OctavioSzMz17/KARD-Client) (frontend)
**Backend en producción:** https://kard-ex9x.onrender.com

---

## 1. Qué es KARD

Plataforma **SaaS marketplace B2B2C** con tres actores y dos modelos de negocio superpuestos:

| Actor | Qué hace |
|---|---|
| **Plataforma** | Administra los negocios dados de alta y cobra suscripciones |
| **Negocios** (tenants) | Restaurantes y ópticas: catálogo, inventario, ventas y tienda pública propia |
| **Clientes** (consumers) | Descubren tiendas en el marketplace, arman carrito y compran |

Un negocio se registra solo, configura su tienda y sube productos; un cliente explora por categoría, entra a la tienda y compra. La plataforma cobra por el software y, a la vez, le lleva clientes al negocio.

---

## 2. Estado actual — qué está construido

### Cifras verificadas

| | Backend | Frontend |
|---|---|---|
| Stack | Python 3.11 · FastAPI · SQLAlchemy 2.0 · PostgreSQL · Redis | Angular 22 · TypeScript 6 · SCSS |
| Módulos | 14 | 14 componentes · 10 servicios · 2 stores |
| Endpoints | 43 en 12 routers | — |
| Tablas | 10 | — |
| Pruebas | 14 casos (pytest) | 17 casos (Vitest) |
| Commits | 26 | 14 |
| Despliegue | ✅ Render, en vivo | ❌ pendiente |

### Funcionalidad terminada

**Mundo del negocio (B2B)**
- Registro self-service: crea tenant, slug único y primer administrador en una operación atómica
- Autenticación JWT con tres roles, bloqueo tras 5 intentos fallidos y bcrypt de 12 rondas
- Catálogo con metadata JSONB validada según el giro (restaurante vs óptica)
- Inventario con bitácora inmutable y alertas de stock bajo
- Recetas (lista de materiales) con descuento en cascada al vender
- Galería de imágenes polimórfica
- Personalización de tienda: colores, logo, banner, contacto y horarios
- **Reporte de ventas** con filtros, gráficas y exportación descargable
- **Cambio de estado de una venta** desde el panel

**Mundo del cliente (B2C)**
- Identidad global: una cuenta sirve en todas las tiendas
- Descubrimiento por categorías y listado de tiendas públicas
- Tienda pública por slug, con caché en Redis
- Carrito por tienda, almacenado en Redis con TTL de 7 días
- **Checkout atómico** con bloqueo de filas y descuento en cascada de insumos

**Transversal:** correos transaccionales, auditoría de operaciones, Docker Compose, 4 migraciones SQL, 4 seeders de datos demo.

### Las tres decisiones técnicas que distinguen al proyecto

**1. Dos identidades que nunca se cruzan.** Un usuario de negocio y un cliente final son entidades separadas, con tablas, login y tokens distintos. El claim `type: "consumer"` impide que un token cruce de un mundo al otro. El `tenant_id` sale del JWT verificado en B2B y del slug de la URL en B2C — nunca del cuerpo de la petición.

**2. Un esquema flexible sin perder validación.** Un platillo y un lente necesitan campos distintos. La columna JSONB `metadata` guarda un documento cuya forma depende del par *(giro, tipo de producto)*, validado contra un esquema en la capa de servicio. Añadir una farmacia es agregar una entrada a un diccionario, sin tocar la lógica de productos.

**3. Un checkout que resiste la concurrencia.** Bloquea con `SELECT … FOR UPDATE` el producto y cada insumo de su receta antes de tocar nada, descuenta en cascada, escribe la bitácora y crea el pedido en una sola transacción. Si falta stock, responde 409 con el detalle exacto de qué falta, para que el cliente decida si continúa sin esos productos.

---

## 3. Cómo trabajé — el método

No escribí el código línea por línea. Operé como **arquitecto y director técnico**, con un asistente de IA como implementador. El método, reconstruido a partir del historial de git y de los documentos del repositorio:

### Especificación antes que código

Antes del primer módulo existían tres documentos: las **reglas técnicas** (`Rules.md`, 19 secciones que fijan cómo se estructura cada módulo, cómo se valida, cómo se aísla cada negocio), las **historias de usuario** con criterios de aceptación verificables, y el **esquema de base de datos** completo con sus constraints.

Esa decisión es la que más se nota en el resultado: los 14 módulos repiten exactamente la misma estructura de cuatro archivos (`routes` → `service` → `repository` → `models`) porque estaba escrito de antemano qué va en cada uno. La consistencia no se negoció módulo por módulo.

### Ramas por funcionalidad, integración por pull request

La primera mitad del proyecto siguió un flujo ordenado: `feature/users-tenants`, `feature/products`, `feature/images`, `feature/insumo-schema`, cada una integrada a `dev` y de ahí a `main` mediante pull request.

### Commits que explican el porqué

Formato convencional con cuerpo explicativo. No *"arreglo bug"*, sino:

> `fix(personalization): el popover de edición quedaba recortado`
> *El popover mide 260×126 pero colgaba de contenedores con `overflow:hidden`, así que el input de la URL no se veía ni se podía editar…*

### Verificar en vez de confiar

Un patrón constante: pedir la prueba, no la afirmación. Correr el comando y mostrar la salida de consola, revisar los logs del servidor, confirmar que un cambio llegó a producción antes de darlo por hecho.

### Iterar sobre lo entregado

El reporte de ventas se construyó en tres commits: primero la funcionalidad, luego el aire y la jerarquía visual, luego que cupiera en una hoja sin tocar el borde. No se dio por terminado en la primera versión funcional.

---

## 4. Evaluación honesta de la dirección

### Lo que está bien hecho

**Especificar antes de construir.** Es la decisión de mayor impacto del proyecto. Un sistema de 14 módulos escrito por un asistente de IA sin reglas previas habría salido inconsistente: cada módulo con su propio criterio sobre dónde poner la lógica. Aquí no pasó, y eso es atribuible a la dirección, no a la herramienta.

**Aceptar correcciones técnicas y ejecutarlas.** Caso concreto y verificable: se señaló que la URL del backend estaba escrita a mano en 9 archivos del frontend y que al desplegar habría que cambiarla en los nueve. El commit `bb9eea4` ("centralizar la URL del backend en environments") lo corrigió. El proyecto está desplegado hoy porque esa deuda se pagó a tiempo.

**Rechazar el trabajo innecesario.** Ante la propuesta de migrar los productos a MongoDB para satisfacer una interpretación literal de un requisito, se evaluó el costo real — romper la atomicidad del checkout — y se descartó. En otra ocasión, ante una configuración de CORS ampliada "por si acaso", la instrucción fue revertirla y dejar el estado anterior. Saber decir que no a un cambio es parte del trabajo de dirigir.

**Corregir al asistente cuando se equivoca.** Sucedió al menos una vez de forma documentada: ante la afirmación errónea de que cierto comando no existía, la respuesta fue señalar que sí existía y pedir que se ejecutara. Un director que acepta todo lo que le dice la herramienta no está dirigiendo.

**Terminar el ciclo.** El backend está desplegado y respondiendo en producción. Muchos proyectos de este tipo se quedan en `localhost`.

### Lo que no está bien hecho

**La disciplina de ramas se degradó.** El proyecto empezó con ramas por funcionalidad y pull requests, y terminó con commits directos a `main`. Los últimos nueve commits del backend no pasaron por ninguna rama. Funciona mientras seas uno, pero es exactamente la práctica que no se puede mostrar como ejemplo de trabajo en equipo.

**El equipo es de dos en el papel y de uno en el historial.** La documentación asigna HU-05, HU-07 y HU-08 a un segundo integrante. El historial de ambos repositorios registra un solo contribuyente. Si eso se presenta como trabajo en equipo, el historial lo contradice en treinta segundos.

**No hay integración continua, y se nota.** Las pruebas existen —14 del servidor, 17 del cliente— pero nada las ejecuta automáticamente. La consecuencia es concreta: **hoy la suite del servidor no corre**. Una carpeta `logging/` quedó olvidada en la raíz del proyecto y, al llamarse igual que un módulo de la biblioteca estándar de Python, impide que pytest arranque. Nada la importa, sus archivos están vacíos, y lleva ahí el tiempo suficiente como para que nadie lo note. Con CI, el primer commit roto habría avisado.

**El entorno de producción se llama "development".** El backend desplegado en Render responde `{"status":"ok","env":"development"}`. La variable `ENVIRONMENT` nunca se cambió al desplegar. No rompe nada hoy, pero es el tipo de detalle que delata que el despliegue se hizo con prisa.

**La cobertura de pruebas es correcta pero estrecha.** Seis casos bien elegidos —aislamiento entre negocios, validación de esquema, stock no negativo, sesión, formulario, guards— sobre un sistema de 43 endpoints. Son los casos correctos; simplemente son pocos.

### Veredicto

Como ejercicio de **dirección técnica**, el trabajo está por encima del promedio en lo que más cuesta: definir las reglas antes de escribir, sostenerlas a lo largo de 40 commits, y saber cuándo rechazar un cambio. La arquitectura resultante es coherente y tiene decisiones defendibles con argumentos técnicos reales.

Donde falla es en el **rigor operativo**: sin CI, sin ramas al final, con una regresión silenciosa que rompió las pruebas y un despliegue sin revisar su configuración. Son fallas de proceso, no de criterio — y por eso son las más fáciles de corregir.

---

## 5. Qué falta

| Prioridad | Pendiente | Por qué importa |
|---|---|---|
| **Alta** | Borrar la carpeta `logging/` de la raíz | Las pruebas del servidor no arrancan mientras exista |
| **Alta** | Desplegar el frontend | El backend está en producción, pero no hay interfaz pública que lo use |
| **Alta** | Configurar `_redirects` o `netlify.toml` | Sin eso, recargar en `/dashboard` devuelve 404 en cualquier SPA |
| Media | Integración continua | Que las 31 pruebas corran en cada push |
| Media | Pagos con Stripe | Los pedidos nunca pasan de `pending` a `paid`; es el bloqueante comercial |
| Media | `ENVIRONMENT=production` en Render | Coherencia del despliegue |
| Baja | Ampliar cobertura de pruebas | 43 endpoints, 6 casos documentados |
| Baja | Dashboard de métricas | Hay reporte de ventas, faltan agregados generales |

---

## 6. Cómo presentar esto en el portafolio

**Sé explícito sobre el método.** No escribiste el código a mano, y ocultarlo es una estrategia frágil: colapsa en la primera entrevista técnica donde te pidan explicar una decisión de implementación. Decirlo de frente es más fuerte, porque lo que demuestra el proyecto sí es tuyo y sí es difícil: definir una arquitectura, fijar reglas y sostenerlas, evaluar propuestas técnicas, rechazar las que no convienen, y verificar en vez de confiar.

**Esa es una habilidad con demanda creciente.** Dirigir asistentes de IA para producir software mantenible es un trabajo real, y la mayoría lo hace mal: acepta todo lo que el asistente propone y termina con un sistema incoherente. La evidencia de que aquí no pasó está en el repositorio, y es verificable.

**Lleva tres artefactos a la conversación**, no solo el enlace al repo:
1. **`Rules.md`** — es la prueba de que las reglas existieron antes que el código.
2. **El checkout** (`orders/service.py`) — bloqueo de filas, cascada y transacción atómica. Es la pieza que demuestra que entiendes concurrencia, no solo CRUD.
3. **El backend desplegado** — una URL que responde vale más que cualquier captura.

**Prepara la pregunta incómoda.** Te la van a hacer: *"si no escribiste el código, ¿qué hiciste tú?"* La respuesta honesta y fuerte es un ejemplo concreto: *"decidí que el `tenant_id` nunca se leyera del request, solo del token verificado, y que todo recurso ajeno devolviera 404 en vez de 403 para no filtrar su existencia. Eso está escrito en las reglas desde antes del primer módulo, y se sostiene en los 14."*

**Y no escondas las fallas.** Un portafolio que dice *"no monté CI y por eso una regresión me rompió las pruebas sin que me diera cuenta"* es mucho más creíble —y más interesante en una entrevista— que uno que presenta un proyecto sin defectos.
