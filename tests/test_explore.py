"""
Pruebas del listado público de negocios (explore).

  PS-15  Cada tarjeta lleva el banner del negocio además de su logo, y un
         negocio sin personalización sigue apareciendo sin romperse.
  PS-17  El listado y las categorías se sirven de Redis; las búsquedas no se
         guardan; un cambio visible en un negocio vacía la caché; y si Redis
         falla, explorar sigue funcionando desde la base.

Sin red ni base: el repositorio y Redis son dobles.
"""

from types import SimpleNamespace

import pytest

from core.redis_client import (
    explore_categories_key,
    explore_list_key,
    invalidate_explore_cache,
)
from explore.service import ExploreService
from tests.test_storefront_cache import BrokenRedis, FakeRedis


class FakeRepo:
    def __init__(self, tenants):
        self._tenants = tenants
        self.list_calls = 0
        self.category_calls = 0

    def list_stores(self, **kwargs):
        self.list_calls += 1
        return self._tenants, len(self._tenants)

    def get_categories(self):
        self.category_calls += 1
        return [{"business_type": "restaurant", "store_count": 2}]


def negocio(slug, config=None, tipo="restaurant"):
    return SimpleNamespace(slug=slug, business_name=slug.title(), business_type=tipo, config=config)


def servicio(*tenants) -> ExploreService:
    svc = ExploreService.__new__(ExploreService)   # sin sesión: solo se usa el repositorio
    svc.repo = FakeRepo(list(tenants))
    return svc


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    """
    Redis de mentira en todas las pruebas: sin esto el servicio intentaría
    conectarse al Redis real (cinco segundos de espera si no existe).
    """
    fake = FakeRedis()
    monkeypatch.setattr("explore.service.get_redis", lambda: fake)
    monkeypatch.setattr("core.redis_client.get_redis", lambda: fake)
    return fake


# ═══════════════════════════════════════════════════════════════════
# PS-15 · Contenido de cada tarjeta
# ═══════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════
# PS-17 · Caché de explorar
# ═══════════════════════════════════════════════════════════════════


class TestCacheDeExplorar:
    def test_las_categorias_se_consultan_una_sola_vez(self):
        svc = servicio(negocio("a"))

        primera = svc.get_categories()
        segunda = svc.get_categories()

        assert svc.repo.category_calls == 1
        assert segunda == primera

    def test_el_mismo_listado_sale_de_redis(self):
        svc = servicio(negocio("a"), negocio("b"))

        primero = svc.list_stores("restaurant", None, 1, 20)
        segundo = svc.list_stores("restaurant", None, 1, 20)

        assert svc.repo.list_calls == 1
        assert segundo == primero

    def test_otra_categoria_o_pagina_es_otra_clave(self):
        """Si se mezclaran, quien pida Ópticas vería los Restaurantes que otro pidió antes."""
        svc = servicio(negocio("a"))

        svc.list_stores("restaurant", None, 1, 20)
        svc.list_stores("optician", None, 1, 20)
        svc.list_stores("restaurant", None, 2, 20)
        svc.list_stores(None, None, 1, 20)

        assert svc.repo.list_calls == 4

    def test_las_busquedas_no_se_guardan(self, redis):
        """Texto libre: cada término distinto sería una clave nueva en un endpoint público."""
        svc = servicio(negocio("a"))

        svc.list_stores(None, "sab", 1, 20)
        svc.list_stores(None, "sab", 1, 20)

        assert svc.repo.list_calls == 2
        assert [k for k in redis.data if k.startswith("explore:list")] == []

    def test_se_guarda_en_las_claves_esperadas(self, redis):
        svc = servicio(negocio("a"))

        svc.get_categories()
        svc.list_stores("restaurant", None, 1, 20)

        assert explore_categories_key() in redis.data
        assert explore_list_key("restaurant", 1, 20) in redis.data

    def test_invalidar_vacia_explorar_pero_no_la_tienda(self, redis):
        svc = servicio(negocio("a"))
        svc.get_categories()
        svc.list_stores(None, None, 1, 20)
        redis.data["storefront:info:sabor"] = "{}"

        invalidate_explore_cache()

        assert [k for k in redis.data if k.startswith("explore:")] == []
        assert "storefront:info:sabor" in redis.data   # cada capa se invalida por separado

    def test_tras_invalidar_se_vuelve_a_leer_la_base(self):
        svc = servicio(negocio("a"))
        svc.list_stores(None, None, 1, 20)

        invalidate_explore_cache()
        svc.list_stores(None, None, 1, 20)

        assert svc.repo.list_calls == 2

    def test_sin_redis_explorar_sigue_funcionando(self, monkeypatch):
        monkeypatch.setattr("explore.service.get_redis", lambda: None)
        svc = servicio(negocio("a"))

        r = svc.list_stores(None, None, 1, 20)
        c = svc.get_categories()

        assert [i.slug for i in r.items] == ["a"]
        assert c.categories[0].business_type == "restaurant"

    def test_un_redis_que_falla_se_ve_como_un_fallo_de_cache(self, monkeypatch):
        monkeypatch.setattr("explore.service.get_redis", lambda: BrokenRedis())
        svc = servicio(negocio("a"))

        r = svc.list_stores(None, None, 1, 20)

        assert r.total == 1


class TestCambiosDelNegocioVacianExplorar:
    """El dueño cambia su logo, banner o eslogan: explorar debe mostrarlo ya, no en 5 minutos."""

    def test_guardar_la_personalizacion_vacia_explorar(self, monkeypatch):
        from settings import service as settings_module

        llamadas = []
        monkeypatch.setattr(settings_module, "invalidate_explore_cache", lambda: llamadas.append("explorar"))
        monkeypatch.setattr(settings_module, "invalidate_storefront_cache", lambda slug: llamadas.append(f"tienda:{slug}"))

        svc = settings_module.SettingsService.__new__(settings_module.SettingsService)
        svc.db = SimpleNamespace(commit=lambda: None)
        svc.tenants = SimpleNamespace(save=lambda t: None)
        tenant = SimpleNamespace(slug="sabor", config={})

        svc._commit_config(tenant, "branding", {"logo_url": "https://x/nuevo.png"})

        assert "explorar" in llamadas and "tienda:sabor" in llamadas
        assert tenant.config["branding"]["logo_url"] == "https://x/nuevo.png"
