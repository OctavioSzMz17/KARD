"""El nombre del producto sale de BRAND_NAME: cambiar de marca es cambiar una variable."""

import pytest

from core.config import settings
from notifications import templates
from notifications.templates import _domain

NOMBRE = "Marca Nueva"


@pytest.fixture
def otra_marca(monkeypatch):
    monkeypatch.setattr(settings, "BRAND_NAME", NOMBRE)
    monkeypatch.setattr(settings, "EMAIL_FROM_NAME", "")


CORREOS = {
    "bienvenida_negocio": lambda: templates.tenant_welcome(business_name="Café Sol", slug="cafe-sol"),
    "bienvenida_cliente": lambda: templates.consumer_welcome(name="Eva"),
    "recuperar": lambda: templates.password_reset(name="Eva", link="https://x.mx/restablecer#token=abc"),
    "cambio": lambda: templates.password_changed(name="Eva"),
    "stock": lambda: templates.low_stock_alert(product_name="Pan", current_stock=2, business_name="Café Sol"),
    "pago_ok": lambda: templates.payment_confirmed(business_name="Café Sol", plan="pro"),
    "pago_falla": lambda: templates.payment_failed(business_name="Café Sol", plan="pro"),
}


class TestNombreDeLaMarca:
    @pytest.mark.parametrize("cual", CORREOS)
    def test_el_correo_usa_el_nombre_configurado_y_no_el_de_antes(self, otra_marca, cual):
        tpl = CORREOS[cual]()
        assert NOMBRE in tpl["html"]
        assert "KARD" not in tpl["html"] and "KARD" not in tpl["subject"]

    def test_el_asunto_lleva_el_nombre_configurado(self, otra_marca):
        assert NOMBRE in templates.password_reset(name=None, link="https://x.mx/r")["subject"]
        assert NOMBRE in templates.password_changed(name=None)["subject"]

    def test_el_nombre_no_puede_meter_html(self, monkeypatch):
        monkeypatch.setattr(settings, "BRAND_NAME", "<b>X</b>")
        html = templates.password_changed(name=None)["html"]
        assert "<b>X</b>" not in html and "&lt;b&gt;X&lt;/b&gt;" in html

    def test_la_direccion_del_pie_sale_de_app_url(self, monkeypatch):
        monkeypatch.setattr(settings, "APP_URL", "https://mi-marca.example/")
        assert _domain() == "mi-marca.example"
        assert "mi-marca.example" in templates.password_changed(name=None)["html"]


class TestRemitente:
    def test_sin_nombre_propio_usa_el_de_la_marca(self, otra_marca):
        assert settings.email_sender_name == NOMBRE

    def test_un_nombre_propio_manda_sobre_la_marca(self, otra_marca, monkeypatch):
        monkeypatch.setattr(settings, "EMAIL_FROM_NAME", "Equipo Soporte")
        assert settings.email_sender_name == "Equipo Soporte"

    def test_el_valor_de_antes_no_cambia(self):
        # Con la configuración por omisión el remitente sigue siendo "KARD".
        assert type(settings).model_fields["BRAND_NAME"].default == "KARD"
