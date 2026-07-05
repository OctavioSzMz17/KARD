# Diagrama de Casos de Uso — CRUD de Productos

## Actores

| Actor | Descripción |
|---|---|
| **Business Admin** | Administrador del negocio. Acceso completo al catálogo e inventario de su tenant. |
| **Staff** | Empleado. Solo lectura y operaciones de inventario (vender / reabastecer). |

---

## Diagrama

```mermaid
graph LR

    BA(["🏢\nBusiness Admin"])
    ST(["👤\nStaff"])

    subgraph PROD ["📦 Gestión de Productos  ·  /api/v1/products"]
        direction TB
        UC1["Crear Producto"]
        UC2["Listar Productos"]
        UC3["Ver Detalle de Producto"]
        UC4["Actualizar Producto"]
        UC5["Eliminar Producto\n(soft delete)"]
        UC6["Vender Producto\n(reducir stock)"]
        UC7["Reabastecer Producto\n(aumentar stock)"]
        UC8["Ver Alertas de Stock Bajo"]
        UC9["Buscar / Filtrar\npor nombre o tipo"]

        UC2 -.->|«extend»| UC9
    end

    BA --> UC1
    BA --> UC2
    BA --> UC3
    BA --> UC4
    BA --> UC5
    BA --> UC6
    BA --> UC7
    BA --> UC8

    ST --> UC2
    ST --> UC3
    ST --> UC6
    ST --> UC7
    ST --> UC8
```

---

## Casos de Uso

| # | Caso de Uso | Business Admin | Staff | Método | Endpoint |
|---|---|:---:|:---:|---|---|
| UC-1 | **Crear Producto** | ✅ | ❌ | `POST` | `/api/v1/products` |
| UC-2 | **Listar Productos** | ✅ | ✅ | `GET` | `/api/v1/products` |
| UC-3 | **Ver Detalle de Producto** | ✅ | ✅ | `GET` | `/api/v1/products/{id}` |
| UC-4 | **Actualizar Producto** | ✅ | ❌ | `PATCH` | `/api/v1/products/{id}` |
| UC-5 | **Eliminar Producto** | ✅ | ❌ | `DELETE` | `/api/v1/products/{id}` |
| UC-6 | **Vender Producto** | ✅ | ✅ | `POST` | `/api/v1/products/{id}/sell` |
| UC-7 | **Reabastecer Producto** | ✅ | ✅ | `POST` | `/api/v1/products/{id}/restock` |
| UC-8 | **Ver Alertas de Stock Bajo** | ✅ | ✅ | `GET` | `/api/v1/products/low-stock` |
| UC-9 | **Buscar / Filtrar** | ✅ | ✅ | — | Query params: `search`, `product_type` |
