"""
Fixtures y dobles de prueba compartidos (Rules.md sección 15).

Las pruebas de esta suite ejercitan la **capa de servicio real** —donde
viven las reglas de negocio— sustituyendo únicamente el repositorio y la
sesión de base de datos. Eso es posible gracias al principio de inversión
de dependencias: `ProductService` no conoce SQLAlchemy, solo llama métodos
del repositorio, así que puede probarse sin levantar PostgreSQL ni Docker.

Los dobles imitan el contrato real, incluido el filtro por `tenant_id`:
`get_by_id` devuelve None cuando el producto pertenece a otro negocio,
igual que la consulta real (products/repository.py).
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dependencies import CurrentUser


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------


def apply_orm_defaults(obj) -> None:
    """
    Imita lo que hace la base al guardar: rellena las columnas vacías con el
    `default` declarado en el modelo (created_at, is_deleted, ...). Sin esto,
    un doble que "guarda" dejaría esos campos en None y la prueba fallaría por
    una diferencia con la base real, no por un error del código.
    """
    for col in obj.__table__.columns:
        if getattr(obj, col.key, None) is None and col.default is not None:
            arg = col.default.arg
            setattr(obj, col.key, arg(None) if callable(arg) else arg)


class FakeProductRepository:
    """
    Repositorio en memoria que respeta el aislamiento por tenant.

    Registra en `calls` con qué tenant_id se invocó cada consulta, para poder
    afirmar que el servicio siempre lo toma del token y nunca de otro origen.
    """

    def __init__(self, products=None):
        self._products = list(products or [])
        self.calls = []
        self.saved = []

    def get_by_id(self, tenant_id, product_id):
        self.calls.append(("get_by_id", tenant_id, product_id))
        for p in self._products:
            if p.id == product_id and p.tenant_id == tenant_id and not p.is_deleted:
                return p
        return None

    def get_by_name(self, tenant_id, name):
        self.calls.append(("get_by_name", tenant_id, name))
        for p in self._products:
            if p.name == name and p.tenant_id == tenant_id:
                return p
        return None

    def save(self, product):
        self.saved.append(product)

    def create(self, product):
        apply_orm_defaults(product)
        self._products.append(product)

    def get_by_code(self, tenant_id, field, value):
        """Igual que el real: acotado al negocio, solo vigentes, SKU sin distinguir mayúsculas."""
        for p in self._products:
            if p.tenant_id != tenant_id or p.is_deleted:
                continue
            actual = getattr(p, field, None)
            if actual is None:
                continue
            if (actual.lower() == value.lower()) if field == "sku" else (actual == value):
                return p
        return None

    def list_products(self, tenant_id, page, page_size, search=None, product_type=None):
        items = [p for p in self._products if p.tenant_id == tenant_id and not p.is_deleted]
        return items, len(items)


class FakeInventoryRepository:
    """Acumula las transacciones de inventario que el servicio intenta escribir."""

    def __init__(self):
        self.created = []

    def create(self, transaction):
        self.created.append(transaction)


class FakeImageRepository:
    """Cuenta cuántas consultas de imágenes se hacen, para vigilar el N+1."""

    def __init__(self):
        self.single_calls = 0
        self.batch_calls = 0

    def list_for_owner(self, *args, **kwargs):
        self.single_calls += 1
        return []

    def list_for_owners(self, tenant_id, owner_type, owner_ids):
        self.batch_calls += 1
        return {oid: [] for oid in owner_ids}

    def list_for_product(self, *args, **kwargs):
        return []


class FakeSession:
    """Sesión mínima: cuenta commits y rollbacks para verificar atomicidad."""

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None


# ---------------------------------------------------------------------------
# Fábricas de datos
# ---------------------------------------------------------------------------


def make_product(product_id, tenant_id, name="Producto", stock=10, product_type="final", metadata=None):
    """Producto mínimo con los atributos que la capa de servicio consulta."""
    return SimpleNamespace(
        id=product_id,
        tenant_id=tenant_id,
        product_type=product_type,
        name=name,
        description=None,
        price=100,
        inventory_count=stock,
        low_stock_threshold=5,
        sku=None, barcode=None,
        metadata_=metadata or {},
        is_deleted=False,
        created_by="u_test",
    )


@pytest.fixture
def tenant_a_user():
    """Usuario autenticado del negocio A (restaurante)."""
    return CurrentUser(
        user_id="u_aaa", tenant_id="t_aaa", business_type="restaurant",
        role="business_admin", email="admin@a.test"
    )


@pytest.fixture
def tenant_b_user():
    """Usuario autenticado del negocio B (óptica) — nunca debe ver datos de A."""
    return CurrentUser(
        user_id="u_bbb", tenant_id="t_bbb", business_type="optician",
        role="business_admin", email="admin@b.test"
    )


@pytest.fixture
def service_factory(monkeypatch):
    """
    Construye un ProductService con repositorios falsos.

    Neutraliza los efectos externos (auditoría y caché de Redis) para que la
    prueba no dependa de infraestructura.
    """
    from products import service as service_module

    monkeypatch.setattr(service_module, "log_operation", lambda **kwargs: None)

    def _build(products=None):
        svc = service_module.ProductService.__new__(service_module.ProductService)
        svc.db = FakeSession()
        svc.products = FakeProductRepository(products)
        svc.inventory = FakeInventoryRepository()
        svc.image_repo = FakeImageRepository()
        svc._bust_storefront_cache = lambda tenant_id: None
        return svc

    return _build
