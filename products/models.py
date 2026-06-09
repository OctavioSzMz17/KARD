"""
Pydantic request/response schemas for the products module
(Rules.md sections 6 & 9 — validation at the entry point).

Also home to the JSONB metadata schemas and `validate_metadata()`
(Rules.md section 8 — JSONB Schema Enforcement): the *shape* of a
product's `metadata` depends on both the tenant's `business_type` AND
the product's `product_type` — a restaurant platillo (final) and a
restaurant insumo require completely different fields.
"""

from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate as jsonschema_validate
from pydantic import BaseModel, Field, field_validator

from core.exceptions import InvalidMetadataError

# ---------------------------------------------------------------------------
# Enums (mirror the Postgres enum types defined in kard_database.sql)
# ---------------------------------------------------------------------------


class ProductType(str, Enum):
    """Mirrors `product_type_enum`: raw input vs. sellable finished product."""

    insumo = "insumo"
    final = "final"


# ---------------------------------------------------------------------------
# JSONB metadata schemas — keyed by (business_type, product_type) so that
# a restaurant platillo (final) and a restaurant insumo get completely
# different required fields. (Rules.md §8)
#
# `additionalProperties: False` rejects any key not declared in the schema.
# `required` covers only fields the business logic depends on; optional
# fields (e.g. `alergenos`) are declared in `properties` but omitted from
# `required`.
# ---------------------------------------------------------------------------

# restaurant + final — platillo listo para el cliente
RESTAURANT_FINAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "categoria": {
            "type": "string",
            "enum": ["entrada", "plato_fuerte", "postre", "bebida", "guarnicion"],
        },
        "tiempo_preparacion_min": {"type": "integer", "minimum": 0},
        "picante": {"type": "boolean"},
        "alergenos": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["categoria", "tiempo_preparacion_min", "picante"],
    "additionalProperties": False,
}

# restaurant + insumo — ingrediente / supply de cocina
RESTAURANT_INSUMO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "unidad_medida": {"type": "string", "minLength": 1},
        "perecedero": {"type": "boolean"},
        "dias_expiracion": {"type": "integer", "minimum": 0},
    },
    "required": ["unidad_medida", "perecedero", "dias_expiracion"],
    "additionalProperties": False,
}

# optician — lente terminado para el cliente (mismo schema para final e insumo)
OPTICIAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "esfera": {"type": "string", "minLength": 1},
        "cilindro": {"type": "string", "minLength": 1},
        "eje": {"type": "integer", "minimum": 0, "maximum": 180},
        "material": {"type": "string", "minLength": 1},
    },
    "required": ["esfera", "cilindro", "eje", "material"],
    "additionalProperties": False,
}

# (business_type, product_type) -> schema
# Missing keys (e.g. "platform") mean that business type cannot own products.
_METADATA_SCHEMAS: dict[tuple[str, str], dict[str, Any]] = {
    ("restaurant", "final"):  RESTAURANT_FINAL_SCHEMA,
    ("restaurant", "insumo"): RESTAURANT_INSUMO_SCHEMA,
    ("optician",   "final"):  OPTICIAN_SCHEMA,
    ("optician",   "insumo"): OPTICIAN_SCHEMA,
}


def validate_metadata(business_type: str, product_type: str, metadata: dict[str, Any]) -> None:
    """
    Validate `metadata` against the schema for (business_type, product_type).

    Raises `InvalidMetadataError` (-> 422) when:
      - the combination has no registered schema (e.g. business_type
        "platform" — the SaaS tenant doesn't sell products), or
      - `metadata` doesn't conform to the required shape.
    """
    schema = _METADATA_SCHEMAS.get((business_type, product_type))
    if schema is None:
        raise InvalidMetadataError(
            f"Products are not supported for business_type '{business_type}'"
        )

    try:
        jsonschema_validate(instance=metadata, schema=schema)
    except JSONSchemaValidationError as exc:
        raise InvalidMetadataError(f"Invalid metadata: {exc.message}") from exc


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ProductCreate(BaseModel):
    """Payload for `POST /products`."""

    product_type: ProductType
    name: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    price: Decimal = Field(..., ge=0, max_digits=10, decimal_places=2)
    inventory_count: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


class ProductUpdate(BaseModel):
    """
    Payload for `PATCH /products/{product_id}`.

    Every field is optional (PATCH = partial update); `None` means
    "leave unchanged". Deliberately excludes `inventory_count` — stock
    only ever changes through `/sell` and `/restock`, which keep the
    `inventory_transactions` audit trail consistent (Rules.md §11).
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    low_stock_threshold: Optional[int] = Field(default=None, ge=0)
    metadata: Optional[dict[str, Any]] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


class StockChangeRequest(BaseModel):
    """Payload for `POST /products/{product_id}/sell` and `/restock`."""

    quantity: int = Field(..., gt=0, description="Units to sell/restock — always a positive count")
    notes: Optional[str] = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Response schemas — never expose internal fields (Rules.md §6.4): no
# tenant-internal bookkeeping beyond what the owning tenant already knows.
# ---------------------------------------------------------------------------


class ProductResponse(BaseModel):
    """Returned by all single-product endpoints (create/get/update/sell/restock)."""

    product_id: str
    tenant_id: str
    product_type: str
    name: str
    description: Optional[str] = None
    price: Decimal
    inventory_count: int
    low_stock_threshold: int
    metadata: dict[str, Any]
    is_deleted: bool
    images: list["ImageResponse"] = Field(default_factory=list)


# Forward-reference resolved at the bottom of this module after ImageResponse
# is imported — avoids a circular import while keeping the response shape
# self-contained inside the products contract.
from images.models import ImageResponse  # noqa: E402 — intentional late import


class ProductListResponse(BaseModel):
    """Returned by `GET /products` — paginated + searchable (Rules.md §5)."""

    items: list[ProductResponse]
    total: int
    page: int
    page_size: int


class StockChangeResponse(BaseModel):
    """Returned by `POST /products/{product_id}/sell` and `/restock`."""

    product_id: str
    action: str
    quantity_change: int
    quantity_before: int
    quantity_after: int
    inventory_count: int
