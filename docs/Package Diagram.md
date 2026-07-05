# Diagrama de Paquetes — KARD Backend

Muestra la organización modular del backend y las dependencias entre paquetes.

---

## Vista General

![Diagrama de Paquetes - KARD Backend](./package_diagram.png)

---

## Diagrama (Mermaid interactivo)

```mermaid
graph TD

    MAIN["📄 main.py\nFastAPI app · CORS\nException handlers · Router registration"]

    subgraph FEATURES ["⚙️ Módulos de Negocio"]
        direction LR
        subgraph PKG_PRODUCTS ["📦 products"]
            P1[routes.py]
            P2[service.py]
            P3[repository.py]
            P4[models.py]
        end
        subgraph PKG_IMAGES ["🖼️ images"]
            I1[routes.py]
            I2[service.py]
            I3[repository.py]
            I4[models.py]
        end
        subgraph PKG_AUTH ["🔐 auth"]
            A1[routes.py]
            A2[service.py]
            A3[repository.py]
            A4[models.py]
        end
        subgraph PKG_SAAS ["🏗️ saasadmin"]
            S1[routes.py]
            S2[service.py]
            S3[models.py]
        end
    end

    subgraph INFRA ["🏛️ Infraestructura Compartida"]
        direction LR
        subgraph PKG_CORE ["⚡ core"]
            C1[config.py]
            C2[dependencies.py]
            C3[exceptions.py]
            C4[security.py]
            C5[redis_client.py]
        end
        subgraph PKG_DB ["🗄️ db"]
            D1[database.py]
            D2[models.py\nTenant · User · Product\nInventoryTransaction · Image]
        end
        subgraph PKG_AUDIT ["📋 audit_logging"]
            AL1[audit.py]
            AL2[logger.py]
        end
        subgraph PKG_UTILS ["🔧 utils"]
            U1[helpers.py]
        end
    end

    %% main.py registra routers
    MAIN -->|include_router| PKG_PRODUCTS
    MAIN -->|include_router| PKG_IMAGES
    MAIN -->|include_router| PKG_AUTH
    MAIN -->|include_router| PKG_SAAS

    %% products
    PKG_PRODUCTS -->|CurrentUser, exceptions| PKG_CORE
    PKG_PRODUCTS -->|Product, InventoryTransaction| PKG_DB
    PKG_PRODUCTS -->|log_operation| PKG_AUDIT
    PKG_PRODUCTS -->|generate_id| PKG_UTILS
    PKG_PRODUCTS -->|ImageRepository| PKG_IMAGES

    %% images
    PKG_IMAGES -->|CurrentUser| PKG_CORE
    PKG_IMAGES -->|Image| PKG_DB
    PKG_IMAGES -->|log_operation| PKG_AUDIT
    PKG_IMAGES -->|generate_id| PKG_UTILS

    %% auth
    PKG_AUTH -->|security, exceptions| PKG_CORE
    PKG_AUTH -->|User, Tenant| PKG_DB
    PKG_AUTH -->|log_auth_event| PKG_AUDIT
    PKG_AUTH -->|generate_id| PKG_UTILS

    %% saasadmin
    PKG_SAAS -->|require_role| PKG_CORE
    PKG_SAAS -->|Tenant| PKG_DB
    PKG_SAAS -->|log_operation| PKG_AUDIT
    PKG_SAAS -->|generate_id| PKG_UTILS

    %% core
    PKG_CORE -->|User model| PKG_DB
```

---

## Descripción de Paquetes

### 📄 `main.py` — Punto de entrada
Arranca la aplicación FastAPI. Registra todos los routers de feature con su prefijo `/api/v1/...`, configura CORS y mapea excepciones de dominio a respuestas HTTP.

---

### ⚙️ Módulos de Negocio

| Paquete | Responsabilidad | Router prefix |
|---|---|---|
| **`products`** | Catálogo de productos + gestión de inventario (venta, reabastecimiento, alertas) | `/api/v1/products` |
| **`images`** | Imágenes asociadas a productos (CRUD de assets URL-based) | `/api/v1/products/{id}/images` |
| **`auth`** | Registro de usuarios, login, JWT, bloqueo anti-fuerza bruta | `/api/v1/auth` |
| **`saasadmin`** | Gestión de tenants (solo `saas_admin`) | `/api/v1/saasadmin` |

Cada módulo sigue la misma arquitectura interna en capas:
```
routes.py  →  service.py  →  repository.py  →  db/models.py
```

---

### 🏛️ Infraestructura Compartida

| Paquete | Contenido | Usado por |
|---|---|---|
| **`core`** | `config`, `dependencies` (JWT/CurrentUser), `exceptions`, `security` (hash/token), `redis_client` | Todos los módulos |
| **`db`** | `database.py` (sesión SQLAlchemy), `models.py` (ORM: Tenant, User, Product, InventoryTransaction, Image) | Todos los módulos |
| **`audit_logging`** | `audit.py` (log_operation, log_auth_event), `logger.py` (setup) | products, images, auth, saasadmin |
| **`utils`** | `helpers.py` (generate_id) | products, images, auth, saasadmin |

---

## Reglas de Dependencia

> [!IMPORTANT]
> Las dependencias fluyen **siempre hacia abajo** en la jerarquía. Ningún paquete de infraestructura (`core`, `db`, `utils`) depende de los módulos de negocio.

> [!NOTE]
> `products` depende de `images` únicamente para leer el `ImageRepository` al construir el `ProductResponse`. Esta es la única dependencia **lateral** entre módulos de negocio.
