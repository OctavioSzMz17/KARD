"""
Pruebas del servidor — casos PS-01, PS-02 y PS-03 del plan IEEE 829
(ver docs/Pruebas_IEEE829.md).

Cubren las tres reglas de negocio que, si se rompen, comprometen el
producto entero:

  PS-01  Un negocio no puede leer productos de otro negocio.
  PS-02  La metadata JSONB debe respetar el esquema de su giro.
  PS-03  El stock nunca puede quedar por debajo de cero.

Ejecución:  pytest tests/ -v
"""

import pytest

from core.exceptions import InsufficientStockError, InvalidMetadataError, ProductNotFoundError
from products.models import StockChangeRequest, validate_metadata
from tests.conftest import make_product


# ═══════════════════════════════════════════════════════════════════
# PS-01 · Aislamiento multi-tenant
# ═══════════════════════════════════════════════════════════════════


class TestAislamientoMultiTenant:
    """PS-01 — Rules.md sección 7: lo ajeno es indistinguible de lo inexistente."""

    def test_no_puede_leer_producto_de_otro_negocio(self, service_factory, tenant_a_user, tenant_b_user):
        """
        El negocio B pide un producto que pertenece al negocio A.

        Resultado esperado: ProductNotFoundError (que la capa HTTP traduce
        a 404, nunca a 403 — un 403 confirmaría que el recurso existe).
        """
        producto_de_a = make_product("p_secreto", tenant_a_user.tenant_id, name="Receta secreta")
        svc = service_factory([producto_de_a])

        with pytest.raises(ProductNotFoundError):
            svc.get_product("p_secreto", tenant_b_user)

    def test_el_dueno_si_lee_su_propio_producto(self, service_factory, tenant_a_user):
        """Control de la prueba anterior: el mismo id sí es visible para su dueño."""
        producto_de_a = make_product("p_secreto", tenant_a_user.tenant_id, name="Receta secreta")
        svc = service_factory([producto_de_a])

        resultado = svc.get_product("p_secreto", tenant_a_user)

        assert resultado.product_id == "p_secreto"
        assert resultado.tenant_id == tenant_a_user.tenant_id

    def test_la_consulta_usa_el_tenant_del_token(self, service_factory, tenant_b_user):
        """
        El tenant_id que llega al repositorio debe ser el del token verificado.

        Es la garantía estructural del aislamiento: aunque el atacante controle
        el product_id de la URL, no controla el tenant contra el que se consulta.
        """
        svc = service_factory([make_product("p_secreto", "t_aaa")])

        with pytest.raises(ProductNotFoundError):
            svc.get_product("p_secreto", tenant_b_user)

        metodo, tenant_id, product_id = svc.products.calls[0]
        assert metodo == "get_by_id"
        assert tenant_id == tenant_b_user.tenant_id   # el del token, no el del dueño
        assert product_id == "p_secreto"


# ═══════════════════════════════════════════════════════════════════
# PS-02 · Validación del esquema JSONB
# ═══════════════════════════════════════════════════════════════════


class TestValidacionMetadataJSONB:
    """PS-02 — Rules.md sección 8: la flexibilidad de JSONB, con esquema."""

    def test_platillo_valido_pasa(self):
        validate_metadata("restaurant", "final", {
            "categoria": "plato_fuerte",
            "tiempo_preparacion_min": 20,
            "picante": True,
        })

    def test_lente_valido_pasa(self):
        validate_metadata("optician", "final", {
            "esfera": "-2.50", "cilindro": "-0.50", "eje": 90, "material": "Policarbonato",
        })

    def test_rechaza_platillo_sin_campo_obligatorio(self):
        """Falta `picante`, declarado como requerido para restaurant+final."""
        with pytest.raises(InvalidMetadataError):
            validate_metadata("restaurant", "final", {
                "categoria": "postre", "tiempo_preparacion_min": 5,
            })

    def test_rechaza_metadata_del_giro_equivocado(self):
        """Un restaurante no puede guardar graduación de lentes en un platillo."""
        with pytest.raises(InvalidMetadataError):
            validate_metadata("restaurant", "final", {
                "esfera": "-2.50", "cilindro": "0.00", "eje": 0, "material": "CR-39",
            })

    def test_rechaza_campos_no_declarados(self):
        """`additionalProperties: false` impide colar claves arbitrarias."""
        with pytest.raises(InvalidMetadataError):
            validate_metadata("restaurant", "final", {
                "categoria": "bebida", "tiempo_preparacion_min": 3, "picante": False,
                "campo_inventado": "valor",
            })

    def test_rechaza_giro_sin_catalogo(self):
        """El tenant interno de la plataforma no vende productos."""
        with pytest.raises(InvalidMetadataError):
            validate_metadata("platform", "final", {})


# ═══════════════════════════════════════════════════════════════════
# PS-03 · El stock nunca queda negativo
# ═══════════════════════════════════════════════════════════════════


class TestStockNoNegativo:
    """PS-03 — la venta se rechaza antes de tocar la base de datos."""

    def test_rechaza_vender_mas_de_lo_disponible(self, service_factory, tenant_a_user):
        producto = make_product("p_001", tenant_a_user.tenant_id, stock=3)
        svc = service_factory([producto])

        with pytest.raises(InsufficientStockError):
            svc.sell_product("p_001", StockChangeRequest(quantity=5), tenant_a_user)

    def test_la_venta_fallida_no_altera_nada(self, service_factory, tenant_a_user):
        """
        Ni el inventario, ni la bitácora, ni un commit: el rechazo ocurre
        antes de cualquier escritura.
        """
        producto = make_product("p_001", tenant_a_user.tenant_id, stock=3)
        svc = service_factory([producto])

        with pytest.raises(InsufficientStockError):
            svc.sell_product("p_001", StockChangeRequest(quantity=5), tenant_a_user)

        assert producto.inventory_count == 3      # intacto
        assert svc.inventory.created == []        # sin bitácora
        assert svc.db.commits == 0                # sin commit

    def test_venta_valida_descuenta_y_deja_bitacora(self, service_factory, tenant_a_user):
        """La venta correcta mueve stock y escribe su transacción, en un solo commit."""
        producto = make_product("p_001", tenant_a_user.tenant_id, stock=10)
        svc = service_factory([producto])

        resultado = svc.sell_product("p_001", StockChangeRequest(quantity=4), tenant_a_user)

        assert resultado.quantity_before == 10
        assert resultado.quantity_after == 6
        assert producto.inventory_count == 6
        assert svc.db.commits == 1

        movimiento = svc.inventory.created[0]
        assert movimiento.action == "sell"
        assert movimiento.quantity_change == -4
        # La bitácora debe cuadrar: antes + cambio = después (CHECK en la BD)
        assert movimiento.quantity_before + movimiento.quantity_change == movimiento.quantity_after

    def test_vender_exactamente_el_stock_disponible_deja_cero(self, service_factory, tenant_a_user):
        """Caso límite: vender todo es válido; vender uno más, no."""
        producto = make_product("p_001", tenant_a_user.tenant_id, stock=7)
        svc = service_factory([producto])

        resultado = svc.sell_product("p_001", StockChangeRequest(quantity=7), tenant_a_user)

        assert resultado.quantity_after == 0
        assert producto.inventory_count == 0

    def test_no_puede_vender_producto_de_otro_negocio(self, service_factory, tenant_b_user):
        """PS-01 y PS-03 combinados: el aislamiento también protege la escritura."""
        producto_de_a = make_product("p_001", "t_aaa", stock=100)
        svc = service_factory([producto_de_a])

        with pytest.raises(ProductNotFoundError):
            svc.sell_product("p_001", StockChangeRequest(quantity=1), tenant_b_user)

        assert producto_de_a.inventory_count == 100
