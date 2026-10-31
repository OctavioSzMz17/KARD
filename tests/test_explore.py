"""
Pruebas del listado público de negocios (explore).

  PS-15  Cada tarjeta lleva el banner del negocio además de su logo, y un
         negocio sin personalización sigue apareciendo sin romperse.

Sin red ni base: el repositorio es un doble.
"""

from types import SimpleNamespace

from explore.service import ExploreService


class FakeRepo:
    def __init__(self, tenants):
        self._tenants = tenants

    def list_stores(self, **kwargs):
        return self._tenants, len(self._tenants)


def negocio(slug, config=None, tipo="restaurant"):
    return SimpleNamespace(slug=slug, business_name=slug.title(), business_type=tipo, config=config)


def servicio(*tenants) -> ExploreService:
    svc = ExploreService.__new__(ExploreService)   # sin sesión: solo se usa el repositorio
    svc.repo = FakeRepo(list(tenants))
    return svc


class TestTarjetasDelListado:
    def test_incluye_logo_banner_y_eslogan(self):
        t = negocio("sabor", {
            "branding": {"logo_url": "https://x/logo.png", "banner_url": "https://x/banner.jpg"},
            "profile": {"tagline": "Cocina de leña"},
        })

        r = servicio(t).list_stores(None, None, 1, 20)

        item = r.items[0]
        assert item.logo_url == "https://x/logo.png"
        assert item.banner_url == "https://x/banner.jpg"
        assert item.tagline == "Cocina de leña"

    def test_un_negocio_sin_personalizar_no_rompe_el_listado(self):
        """Un negocio nuevo no tiene branding ni perfil: las tres imágenes/textos van vacíos."""
        r = servicio(negocio("nuevo", config=None), negocio("vacio", config={})).list_stores(None, None, 1, 20)

        assert [i.slug for i in r.items] == ["nuevo", "vacio"]
        assert all(i.logo_url is None and i.banner_url is None for i in r.items)

    def test_conserva_el_total_y_la_paginacion(self):
        r = servicio(negocio("a"), negocio("b")).list_stores(None, None, 2, 10)

        assert (r.total, r.page, r.page_size) == (2, 2, 10)
