"""
En un restaurante los platillos no llevan existencias: solo los insumos.

Un platillo se cocina al momento. Lo que se acaba es lo que lleva la receta,
y eso ya se descuenta solo en cada venta. Contar "12 chilaquiles" no tiene
sentido y, peor, un platillo nuevo nacía con 0 y no se podía vender.

Todo lo demás conserva su stock: los insumos del restaurante y los productos
de una óptica (un lente terminado es una pieza que está o no está).
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.exceptions import InsufficientStockError
from db.models import Product, ProductRecipe, Tenant
from orders.service import OrderService
from products.models import StockChangeRequest
from products.stock_policy import tracks_stock
from tests.conftest import make_product


# ═══════════════════════════════════════════════════════════════════
# La regla
# ═══════════════════════════════════════════════════════════════════


class TestReglaDeStock:
    def test_un_platillo_de_restaurante_no_lleva_stock(self):
        assert tracks_stock("restaurant", "final") is False

    def test_un_insumo_de_restaurante_si_lleva_stock(self):
        assert tracks_stock("restaurant", "insumo") is True

    def test_una_optica_lleva_stock_en_todo(self):
        assert tracks_stock("optician", "final") is True
        assert tracks_stock("optician", "insumo") is True

    def test_acepta_el_enum_del_modelo(self):
        from products.models import ProductType
        assert tracks_stock("restaurant", ProductType.final) is False
        assert tracks_stock("restaurant", ProductType.insumo) is True

    def test_sin_tipo_de_negocio_no_cambia_el_comportamiento_de_antes(self):
        assert tracks_stock(None, "final") is True


# ═══════════════════════════════════════════════════════════════════
# Lo que ve el panel
# ═══════════════════════════════════════════════════════════════════


class TestRespuestaDelApi:
    def test_el_platillo_dice_que_no_lleva_stock_y_el_insumo_que_si(self, service_factory, tenant_a_user):
        platillo = make_product("p_plato", "t_aaa", product_type="final")
        insumo = make_product("p_ins", "t_aaa", product_type="insumo")
        svc = service_factory([platillo, insumo])

        assert svc.get_product("p_plato", tenant_a_user).tracks_stock is False
        assert svc.get_product("p_ins", tenant_a_user).tracks_stock is True

    def test_el_listado_marca_cada_producto(self, service_factory, tenant_a_user):
        svc = service_factory([
            make_product("p_plato", "t_aaa", name="Chilaquiles", product_type="final"),
            make_product("p_ins", "t_aaa", name="Aguacate", product_type="insumo"),
        ])
        items = {p.name: p.tracks_stock for p in svc.list_products(tenant_a_user, page=1, page_size=20).items}
        assert items == {"Chilaquiles": False, "Aguacate": True}

    def test_en_una_optica_el_producto_final_si_lleva_stock(self, service_factory, tenant_b_user):
        svc = service_factory([make_product("p_len", "t_bbb", product_type="final")])
        assert svc.get_product("p_len", tenant_b_user).tracks_stock is True


class TestVenderOResurtirUnPlatillo:
    def test_no_se_puede_vender_un_platillo_por_existencias(self, service_factory, tenant_a_user):
        platillo = make_product("p_plato", "t_aaa", stock=0, product_type="final")
        svc = service_factory([platillo])

        with pytest.raises(InsufficientStockError, match="insumos"):
            svc.sell_product("p_plato", StockChangeRequest(quantity=1), tenant_a_user)
        assert svc.db.commits == 0 and svc.inventory.created == []

    def test_no_se_puede_resurtir_un_platillo(self, service_factory, tenant_a_user):
        platillo = make_product("p_plato", "t_aaa", stock=0, product_type="final")
        svc = service_factory([platillo])

        with pytest.raises(InsufficientStockError):
            svc.restock_product("p_plato", StockChangeRequest(quantity=5), tenant_a_user)
        assert platillo.inventory_count == 0 and svc.db.commits == 0

    def test_un_insumo_si_se_resurte(self, service_factory, tenant_a_user):
        insumo = make_product("p_ins", "t_aaa", stock=2, product_type="insumo")
        svc = service_factory([insumo])

        svc.restock_product("p_ins", StockChangeRequest(quantity=5), tenant_a_user)
        assert insumo.inventory_count == 7


class TestAlertasDeStockBajo:
    def test_los_platillos_no_salen_en_stock_bajo(self, service_factory, tenant_a_user):
        platillo = make_product("p_plato", "t_aaa", name="Lasaña", stock=3, product_type="final")
        insumo = make_product("p_ins", "t_aaa", name="Maíz", stock=1, product_type="insumo")
        svc = service_factory([platillo, insumo])
        svc.products.get_low_stock = lambda tenant_id: [platillo, insumo]

        nombres = [p.name for p in svc.get_low_stock(tenant_a_user)]
        assert nombres == ["Maíz"]


# ═══════════════════════════════════════════════════════════════════
# La venta de verdad: análisis y descuento
# ═══════════════════════════════════════════════════════════════════


class _Consulta:
    """Lo que devuelve `db.query(...)`: ignora los filtros y entrega lo que la prueba preparó."""

    def __init__(self, resultados):
        self._resultados = resultados

    def filter(self, *a, **k):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._resultados.pop(0) if self._resultados else None

    def all(self):
        return list(self._resultados)

    def scalar(self):
        return self._resultados[0] if self._resultados else None


class FakeDb:
    def __init__(self, *, business_type, productos=(), recetas=(), insumos=()):
        self.business_type = business_type
        self.productos = list(productos)
        self.recetas = list(recetas)
        self.insumos = list(insumos)
        self.agregados = []

    def query(self, entidad):
        if entidad is Tenant.business_type:
            return _Consulta([self.business_type])
        if entidad is ProductRecipe:
            return _Consulta(self.recetas)
        # Primero va el producto del carrito; los siguientes son sus insumos.
        if self.productos:
            return _Consulta([self.productos.pop(0)])
        return _Consulta([self.insumos.pop(0)] if self.insumos else [])

    def add(self, objeto):
        self.agregados.append(objeto)

    def flush(self):
        pass


def _producto(pid, *, tipo, stock, nombre="Producto", precio="100.00"):
    return SimpleNamespace(
        id=pid, name=nombre, product_type=tipo, inventory_count=stock,
        price=Decimal(precio), metadata_={"unidad_medida": "kg"} if tipo == "insumo" else {},
    )


def _servicio(db):
    svc = OrderService.__new__(OrderService)
    svc.db = db
    return svc


def _carrito(pid, cantidad):
    return {pid: {"quantity": cantidad, "name": "x"}}


class TestVentaDeUnPlatillo:
    def test_un_platillo_sin_receta_y_sin_stock_se_puede_vender(self):
        """Antes nacía con 0 existencias y el cobro lo rechazaba."""
        plato = _producto("p_1", tipo="final", stock=0, nombre="Esquites")
        svc = _servicio(FakeDb(business_type="restaurant", productos=[plato]))

        surtibles, faltantes = svc._analyze_cart("t_1", _carrito("p_1", 3))

        assert faltantes == []
        assert surtibles[0]["tracks_stock"] is False

    def test_un_platillo_falta_solo_si_falta_un_insumo_de_su_receta(self):
        plato = _producto("p_1", tipo="final", stock=0, nombre="Chilaquiles")
        masa = _producto("i_1", tipo="insumo", stock=1, nombre="Maíz")
        receta = SimpleNamespace(insumo_product_id="i_1", quantity=2)
        svc = _servicio(FakeDb(
            business_type="restaurant", productos=[plato], recetas=[receta], insumos=[masa],
        ))

        surtibles, faltantes = svc._analyze_cart("t_1", _carrito("p_1", 1))

        assert surtibles == []
        assert faltantes[0]["reason"] == "ingredient_shortage"   # no "product_shortage"
        assert "available" not in faltantes[0]                    # no hay stock del platillo que mostrar
        assert faltantes[0]["missing_ingredients"][0]["insumo_name"] == "Maíz"

    def test_vender_un_platillo_descuenta_sus_insumos_y_no_su_propio_stock(self):
        plato = _producto("p_1", tipo="final", stock=0, nombre="Chilaquiles")
        masa = _producto("i_1", tipo="insumo", stock=10, nombre="Maíz")
        receta = SimpleNamespace(insumo_product_id="i_1", quantity=2)
        db = FakeDb(business_type="restaurant", productos=[plato], recetas=[receta], insumos=[masa])
        svc = _servicio(db)
        tenant = SimpleNamespace(id="t_1", config=None)

        surtibles, _ = svc._analyze_cart("t_1", _carrito("p_1", 3))
        svc._apply(tenant, surtibles, captured_by="staff", fulfillment_type="takeaway", status="paid")

        assert plato.inventory_count == 0          # el platillo sigue sin existencias
        assert masa.inventory_count == 4           # 10 − (2 × 3)
        movimientos = [o for o in db.agregados if o.__class__.__name__ == "InventoryTransaction"]
        assert [m.product_id for m in movimientos] == ["i_1"]   # solo el insumo deja bitácora


class TestVentaEnUnaOptica:
    def test_el_producto_final_de_una_optica_sigue_rechazandose_sin_stock(self):
        lente = _producto("p_1", tipo="final", stock=1, nombre="Lente")
        svc = _servicio(FakeDb(business_type="optician", productos=[lente]))

        surtibles, faltantes = svc._analyze_cart("t_1", _carrito("p_1", 2))

        assert surtibles == []
        assert faltantes[0]["reason"] == "product_shortage"
        assert faltantes[0]["available"] == 1

    def test_y_al_venderlo_descuenta_su_stock(self):
        lente = _producto("p_1", tipo="final", stock=5, nombre="Lente")
        db = FakeDb(business_type="optician", productos=[lente])
        svc = _servicio(db)
        tenant = SimpleNamespace(id="t_1", config=None)

        surtibles, _ = svc._analyze_cart("t_1", _carrito("p_1", 2))
        svc._apply(tenant, surtibles, captured_by="staff", fulfillment_type="takeaway", status="paid")

        assert lente.inventory_count == 3
