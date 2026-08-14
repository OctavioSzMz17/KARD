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

### PC-02 · Validación del formulario y doble intento de login

| Campo | Contenido |
|---|---|
| **Identificador** | `KARD-TC-PC-02` |
| **Elemento** | `LoginComponent` |
| **Requisito cubierto** | US-001 — login con detección de tenant |
| **Prioridad** | Media |
| **Entradas** | Formulario vacío; correo con formato inválido; credenciales válidas de negocio; credenciales de cliente; credenciales inexistentes |
| **Resultado esperado** | Con formulario inválido no se emite ninguna petición HTTP. Con credenciales válidas se llama a `/auth/login`; si responde 401, se reintenta contra `/consumers/login`; si ambas fallan, se muestra mensaje de error |
| **Criterio de aprobación** | Las seis verificaciones pasan, incluida la ausencia de peticiones en los casos inválidos |
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
| PC-01 · AuthService | 5 | ✅ Aprobado | 4.16 s (suite) |
| PC-02 · LoginComponent | 6 | ✅ Aprobado | — |
| PC-03 · Guards de ruta | 6 | ✅ Aprobado | — |
| **TOTAL** | **31** | **6 de 6 aprobados** | |

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
| Dobles y fixtures | `Kard/tests/conftest.py` |
| PC-01 | `Kard-client/src/app/core/services/auth.service.spec.ts` |
| PC-02 | `Kard-client/src/app/auth/login/login.component.spec.ts` |
| PC-03 | `Kard-client/src/app/core/guards/auth.guard.spec.ts` |
