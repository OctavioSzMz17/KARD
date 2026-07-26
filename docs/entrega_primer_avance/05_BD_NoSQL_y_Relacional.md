# KARD — Diseño de Base de Datos (NoSQL para productos + Relacional completa)
**Entregable:** Primer Avance · Puntos 3 y 4  
**Motor:** PostgreSQL (con columnas JSONB para la parte NoSQL)

---

## 1. Enfoque híbrido

KARD usa **PostgreSQL** como único motor, combinando:
- **Modelo relacional** para las entidades con estructura fija (negocios, usuarios, pedidos).
- **Modelo NoSQL (documental)** mediante columnas **JSONB** para los atributos variables de los productos, que cambian según el tipo de negocio.

> Esto cumple el punto 3 (**BD NoSQL para productos**) y el punto 4 (**BD relacional completa**) con un solo motor, sin duplicar infraestructura.

---

## 2. BD NoSQL para productos (JSONB)

El problema: un producto de **restaurante** y uno de **óptica** necesitan campos distintos. Un esquema rígido con columnas fijas no sirve para ambos.

**Solución:** la columna `metadata` (JSONB) guarda un documento cuya forma depende del `business_type` del negocio y del `product_type`.

### Producto de restaurante (platillo)
```json
{
  "categoria": "plato_fuerte",
  "tiempo_preparacion_min": 20,
  "picante": true,
  "alergenos": ["gluten"]
}
```

### Producto de óptica (lente)
```json
{
  "esfera": "-2.50",
  "cilindro": "-0.50",
  "eje": 90,
  "material": "Policarbonato"
}
```

### Validación de esquema
Aunque JSONB es flexible, KARD **valida la forma** de cada documento en la capa de servicio según la combinación `(business_type, product_type)`. Así se obtiene la flexibilidad del NoSQL con la seguridad de un esquema.

| business_type | product_type | Campos requeridos |
|---|---|---|
| restaurant | final | categoria, tiempo_preparacion_min, picante |
| restaurant | insumo | unidad_medida, perecedero, dias_expiracion |
| optician | final/insumo | esfera, cilindro, eje, material |

**Ventajas de este enfoque:**
- Agregar un nuevo tipo de negocio = agregar un esquema, sin migrar tablas.
- Consultas rápidas sobre JSONB con índices GIN (si se requiere).
- Un solo motor de base de datos que operar y respaldar.

---

## 3. BD Relacional completa

### Diagrama de entidades (resumen)

```
┌──────────────┐        ┌──────────────┐
│   tenants    │1──────*│    users     │
│ (negocios)   │        │ (admins/staff)│
└──────┬───────┘        └──────────────┘
       │1
       │
       │*                ┌──────────────┐
┌──────┴───────┐1──────*│    images    │
│   products   │        │ (polimórfica) │
│ (JSONB meta) │        └──────────────┘
└──────┬───────┘
       │1
       │*
┌──────┴──────────────┐
│ inventory_transactions│  (historial inmutable de stock)
└─────────────────────┘

┌──────────────┐        ┌──────────────┐        ┌──────────────┐
│  consumers   │1──────*│    orders    │1──────*│ order_items  │
│ (clientes    │        │  (pedidos)   │        │ (líneas de   │
│  globales)   │        └──────┬───────┘        │  pedido)     │
└──────────────┘               │*               └──────────────┘
                               │
                        ┌──────┴───────┐
                        │   tenants    │  (a qué negocio se compró)
                        └──────────────┘

┌──────────────────┐    ┌──────────────┐
│ consumer_events  │    │  audit_logs  │
│ (comportamiento) │    │ (auditoría)  │
└──────────────────┘    └──────────────┘
```

### Tablas principales

| Tabla | Descripción | Relaciones clave |
|---|---|---|
| `tenants` | Negocios registrados | 1—* users, 1—* products |
| `users` | Admins/empleados de un negocio | *—1 tenants |
| `products` | Catálogo (con `metadata` JSONB) | *—1 tenants |
| `inventory_transactions` | Historial inmutable de movimientos de stock | *—1 products |
| `images` | Imágenes polimórficas (hoy productos) | por owner_type/owner_id |
| `consumers` | Identidad global del cliente (sin tenant_id) | 1—* orders |
| `orders` | Pedidos del cliente | *—1 consumers, *—1 tenants |
| `order_items` | Líneas de cada pedido (precio congelado) | *—1 orders |
| `consumer_events` | Eventos de comportamiento del cliente | *—1 tenants |
| `audit_logs` | Bitácora de operaciones | — |

### Reglas de integridad destacadas
- **Aislamiento multi-tenant:** todas las tablas de negocio llevan `tenant_id`.
- **`order_items.unit_price`** guarda el precio al momento de la compra (inmutable).
- **`inventory_transactions`** es append-only: `quantity_after = quantity_before + quantity_change` (CHECK en BD).
- **`orders.status`** restringido por CHECK a: `pending | paid | fulfilled | canceled`.
- **Slug único** por tenant para las URLs públicas.

### Archivos de referencia
- Esquema fuente de verdad: [`kard_database.sql`](../../kard_database.sql)
- Migraciones incrementales: [`migrations/`](../../migrations/)
- Modelos ORM (SQLAlchemy): [`db/models.py`](../../db/models.py)
