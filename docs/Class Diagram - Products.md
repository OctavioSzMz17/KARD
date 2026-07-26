# Diagrama de Clases — Módulo de Productos

Cubre las capas: **ORM (DB)**, **Pydantic Schemas**, **Repository** y **Service**.

---

## Vista General

![Diagrama de Clases - Módulo de Productos](./class_diagram_products.png)

---

## Diagrama (Mermaid interactivo)

```mermaid
classDiagram
    direction TB

    %% ══════════════════════════════════════════════════
    %% ENUMS
    %% ══════════════════════════════════════════════════

    class ProductType {
        <<enumeration>>
        insumo
        final
    }

    class InventoryAction {
        <<enumeration>>
        sell
        restock
        adjust
        create
    }

    %% ══════════════════════════════════════════════════
    %% ORM MODELS  (db/models.py)
    %% ══════════════════════════════════════════════════

    class Product {
        <<ORM - tabla: products>>
        +String id  PK
        +String tenant_id  FK
        +ProductType product_type
        +String name
        +String description
        +Decimal price
        +int inventory_count
        +int low_stock_threshold
        +dict metadata_
        +bool is_deleted
        +String created_by  FK
        +DateTime created_at
        +DateTime updated_at
    }

    class InventoryTransaction {
        <<ORM - tabla: inventory_transactions>>
        +BigInt id  PK  autoincrement
        +String product_id  FK
        +String tenant_id  FK
        +InventoryAction action
        +int quantity_change
        +int quantity_before
        +int quantity_after
        +String user_id  FK
        +String notes
        +DateTime created_at
    }

    %% ══════════════════════════════════════════════════
    %% PYDANTIC SCHEMAS  (products/models.py)
    %% ══════════════════════════════════════════════════

    class ProductCreate {
        <<Request Schema>>
        +ProductType product_type
        +str name
        +str description
        +Decimal price
        +int inventory_count
        +int low_stock_threshold
        +dict metadata
        +name_not_blank()
    }

    class ProductUpdate {
        <<Request Schema - PATCH>>
        +str name
        +str description
        +Decimal price
        +int low_stock_threshold
        +dict metadata
        +name_not_blank()
    }

    class StockChangeRequest {
        <<Request Schema>>
        +int quantity
        +str notes
    }

    class ProductResponse {
        <<Response Schema>>
        +str product_id
        +str tenant_id
        +str product_type
        +str name
        +str description
        +Decimal price
        +int inventory_count
        +int low_stock_threshold
        +dict metadata
        +bool is_deleted
        +list~ImageResponse~ images
    }

    class ProductListResponse {
        <<Response Schema>>
        +list~ProductResponse~ items
        +int total
        +int page
        +int page_size
    }

    class StockChangeResponse {
        <<Response Schema>>
        +str product_id
        +str action
        +int quantity_change
        +int quantity_before
        +int quantity_after
        +int inventory_count
    }

    %% ══════════════════════════════════════════════════
    %% REPOSITORY LAYER  (products/repository.py)
    %% ══════════════════════════════════════════════════

    class ProductRepository {
        <<Repository>>
        -Session db
        +get_by_id(tenant_id, product_id) Product
        +get_by_name(tenant_id, name) Product
        +list_products(tenant_id, page, page_size, search, product_type) tuple
        +get_low_stock(tenant_id) list~Product~
        +create(product) Product
        +save(product) Product
    }

    class InventoryTransactionRepository {
        <<Repository>>
        -Session db
        +create(transaction) InventoryTransaction
        +count_for_product(tenant_id, product_id) int
    }

    %% ══════════════════════════════════════════════════
    %% SERVICE LAYER  (products/service.py)
    %% ══════════════════════════════════════════════════

    class ProductService {
        <<Service>>
        -Session db
        -ProductRepository products
        -InventoryTransactionRepository inventory
        -ImageRepository image_repo
        +create_product(payload, current_user) ProductResponse
        +list_products(current_user, page, page_size, search, product_type) ProductListResponse
        +get_product(product_id, current_user) ProductResponse
        +update_product(product_id, payload, current_user) ProductResponse
        +soft_delete_product(product_id, current_user) None
        +sell_product(product_id, payload, current_user) StockChangeResponse
        +restock_product(product_id, payload, current_user) StockChangeResponse
        +get_low_stock(current_user) list~ProductResponse~
        -_apply_stock_change(product, action, quantity_change, ...) StockChangeResponse
        -_require_product(product_id, tenant_id) Product
        -_snapshot(product) dict
        -_to_response(product) ProductResponse
    }

    %% ══════════════════════════════════════════════════
    %% RELACIONES
    %% ══════════════════════════════════════════════════

    %% ORM
    Product "1" --> "0..*" InventoryTransaction : registra movimientos

    %% Enums
    Product --> ProductType : usa
    InventoryTransaction --> InventoryAction : usa

    %% Schemas → ORM
    ProductCreate ..> Product : crea
    ProductUpdate ..> Product : actualiza parcialmente
    StockChangeRequest ..> InventoryTransaction : genera
    ProductResponse ..> Product : proyecta

    %% Aggregation
    ProductListResponse "1" *-- "0..*" ProductResponse : contiene

    %% Repository → ORM
    ProductRepository --> Product : gestiona
    InventoryTransactionRepository --> InventoryTransaction : registra

    %% Service → Repository
    ProductService --> ProductRepository : usa
    ProductService --> InventoryTransactionRepository : usa

    %% Service → Schemas
    ProductService ..> ProductCreate : recibe
    ProductService ..> ProductUpdate : recibe
    ProductService ..> StockChangeRequest : recibe
    ProductService ..> ProductResponse : devuelve
    ProductService ..> ProductListResponse : devuelve
    ProductService ..> StockChangeResponse : devuelve
```

---

## Descripción de Capas

### 🗄️ ORM Models (`db/models.py`)
Mapean directamente a tablas PostgreSQL. `Product` es la entidad central; `InventoryTransaction` es un log **inmutable** de cada movimiento de stock — nunca se actualiza ni elimina.

### 📋 Pydantic Schemas (`products/models.py`)
- **Request**: `ProductCreate`, `ProductUpdate` (PATCH parcial), `StockChangeRequest`
- **Response**: `ProductResponse`, `ProductListResponse`, `StockChangeResponse`
- `inventory_count` está **excluido** de `ProductUpdate` — el stock solo cambia vía `/sell` y `/restock` para mantener la traza de auditoría.

### 🔎 Repository (`products/repository.py`)
Capa de acceso a datos sin lógica de negocio. **Toda query filtra por `tenant_id`** — aislamiento multi-tenant garantizado. `InventoryTransactionRepository` es append-only por diseño.

### ⚙️ Service (`products/service.py`)
Contiene todas las reglas de negocio:
- Valida metadata JSONB contra el schema del `business_type` del tenant
- Previene nombres duplicados por tenant
- Garantiza que el stock nunca baje de cero
- Aplica venta/reabastecimiento + `InventoryTransaction` en una sola transacción atómica
- Escribe el audit log en cada operación

---

## Invariantes Clave

| Regla | Implementación |
|---|---|
| Stock nunca negativo | Check en `sell_product` + constraint DB `products_inventory_non_negative` |
| Nombre único por tenant | Constraint `uq_product_name_tenant` + check en servicio |
| Soft-delete (no hard delete) | `is_deleted = True`, nunca `DELETE` |
| `tenant_id` solo del JWT | Siempre `current_user.tenant_id`, nunca del body |
| Transacción atómica en stock | `product.inventory_count` + `InventoryTransaction` en mismo `db.commit()` |
