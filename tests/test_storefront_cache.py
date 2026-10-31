"""
Pruebas de las capas de caché de la tienda pública.

Cubren lo que, si falla, sirve datos viejos o rompe la tienda:

  PS-11  Resolver el negocio y repetir una página no vuelve a la base de datos.
  PS-12  La analítica no hace esperar la respuesta, pero sigue registrándose.
  PS-13  Un cambio del dueño vacía las tres capas; sin Redis todo sigue andando.
  PS-14  El navegador revalida con ETag y recibe 304 si nada cambió.

No tocan la red ni la base: Redis, el repositorio y la sesión son dobles.
"""

import fnmatch
from decimal import Decimal
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

from core.exceptions import StorefrontNotFoundError
from core.redis_client import invalidate_storefront_cache, storefront_info_key, storefront_tenant_key
from storefront.routes import PUBLIC_CACHE_CONTROL, _with_http_cache
from storefront.service import StorefrontService
from tests.conftest import FakeSession


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class FakeRedis:
    """Redis en memoria con los cuatro comandos que usa la tienda."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    def scan(self, cursor, match="*", count=100):
        return 0, [k for k in self.data if fnmatch.fnmatch(k, match)]


class BrokenRedis(FakeRedis):
    """Redis que falla en cada llamada: la tienda debe degradar, no caerse."""

    def get(self, key):
        raise ConnectionError("redis caído")

    def set(self, key, value, ex=None):
        raise ConnectionError("redis caído")


class FakeProducts:
    def __init__(self, items):
        self.items = items
        self.list_calls = 0

    def list_products(self, **kwargs):
        self.list_calls += 1
        return self.items, len(self.items)

    def get_by_id(self, tenant_id, product_id):
        return next((p for p in self.items if p.id == product_id), None)


class FakeRepo:
    def __init__(self, tenant, items):
        self._tenant = tenant
        self.products = FakeProducts(items)
        self.tenant_lookups = 0
        self.events = []

    def get_public_tenant_by_slug(self, slug):
        self.tenant_lookups += 1
        return self._tenant if slug == self._tenant.slug else None

    def record_event(self, tenant_id, event_type, payload=None, consumer_id=None):
        self.events.append((tenant_id, event_type))


class FakeImageRepo:
    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    def list_for_owners(self, tenant_id, owner_type, owner_ids):
        self.batch_calls += 1
        return {oid: [] for oid in owner_ids}

    def list_for_owner(self, *args, **kwargs):
        self.single_calls += 1
        return []


class FakeTasks:
    """Recoge lo que FastAPI ejecutaría tras enviar la respuesta."""

    def __init__(self):
        self.added = []

    def add_task(self, fn, *args, **kwargs):
        self.added.append((fn.__name__, args))


def producto(pid, nombre="Taco"):
    return SimpleNamespace(
        id=pid, name=nombre, description=None, price=Decimal("85.00"),
        product_type="final", metadata_={"categoria": "plato_fuerte"},
    )


@pytest.fixture
def tienda(monkeypatch):
    """Servicio real con Redis, repositorio y sesión sustituidos."""
    redis = FakeRedis()
    monkeypatch.setattr("storefront.service.get_redis", lambda: redis)

    tenant = SimpleNamespace(
        id="t_aaa", slug="sabor", business_name="Sabor", business_type="restaurant", config={},
    )
    db = FakeSession()
    tasks = FakeTasks()
    svc = StorefrontService(db, tasks)
    svc.repo = FakeRepo(tenant, [producto("p1"), producto("p2", "Flan")])
    svc.image_repo = FakeImageRepo()
    return SimpleNamespace(svc=svc, redis=redis, db=db, tasks=tasks, repo=svc.repo, images=svc.image_repo)


# ═══════════════════════════════════════════════════════════════════
# PS-11 · No volver a la base de datos por lo que ya se sabe
# ═══════════════════════════════════════════════════════════════════


class TestCacheDelNegocioYCatalogo:
    def test_el_negocio_se_resuelve_una_sola_vez(self, tienda):
        """Cada visita pública empezaba con una consulta que casi nunca cambia."""
        tienda.svc.list_products("sabor", 1, 20)
        tienda.svc.list_products("sabor", 2, 20)
        tienda.svc.get_product("sabor", "p1")

        assert tienda.repo.tenant_lookups == 1

    def test_una_pagina_repetida_sale_de_redis(self, tienda):
        primera = tienda.svc.list_products("sabor", 1, 20)
        segunda = tienda.svc.list_products("sabor", 1, 20)

        assert tienda.repo.products.list_calls == 1
        assert segunda == primera

    def test_la_info_del_negocio_sale_de_redis(self, tienda):
        tienda.svc.get_storefront("sabor")
        tienda.svc.get_storefront("sabor")

        assert tienda.repo.tenant_lookups == 1

    def test_las_imagenes_se_piden_en_una_sola_consulta(self, tienda):
        """Antes eran N consultas, una por producto, en cada fallo de caché."""
        tienda.svc.list_products("sabor", 1, 20)

        assert tienda.images.batch_calls == 1
        assert tienda.images.single_calls == 0

    def test_un_slug_inexistente_no_se_cachea(self, tienda):
        """Si se activa después, debe verse al instante y no tras el TTL."""
        with pytest.raises(StorefrontNotFoundError):
            tienda.svc.list_products("no-existe", 1, 20)

        assert storefront_tenant_key("no-existe") not in tienda.redis.data


# ═══════════════════════════════════════════════════════════════════
# PS-12 · La analítica no frena la respuesta
# ═══════════════════════════════════════════════════════════════════


class TestAnaliticaEnSegundoPlano:
    def test_con_tasks_no_escribe_en_linea(self, tienda):
        tienda.svc.list_products("sabor", 1, 20)

        assert tienda.db.commits == 0
        assert tienda.repo.events == []
        assert [nombre for nombre, _ in tienda.tasks.added] == ["record_event_detached"]

    def test_un_acierto_de_cache_tambien_registra_la_visita(self, tienda):
        tienda.svc.list_products("sabor", 1, 20)
        tienda.svc.list_products("sabor", 1, 20)

        assert len(tienda.tasks.added) == 2

    def test_sin_tasks_se_escribe_en_linea_como_antes(self, tienda):
        tienda.svc.tasks = None
        tienda.svc.list_products("sabor", 1, 20)

        assert tienda.repo.events == [("t_aaa", "view_catalog")]
        assert tienda.db.commits == 1


# ═══════════════════════════════════════════════════════════════════
# PS-13 · Invalidación y degradación
# ═══════════════════════════════════════════════════════════════════


class TestInvalidacionYDegradacion:
    def test_invalidar_vacia_las_tres_capas(self, tienda, monkeypatch):
        tienda.svc.get_storefront("sabor")
        tienda.svc.list_products("sabor", 1, 20)
        tienda.svc.list_products("sabor", 1, 50)
        otro = "storefront:catalog:otro-negocio:page:1:size:20:search:"
        tienda.redis.data[otro] = "{}"
        assert storefront_tenant_key("sabor") in tienda.redis.data
        assert storefront_info_key("sabor") in tienda.redis.data

        monkeypatch.setattr("core.redis_client.get_redis", lambda: tienda.redis)
        invalidate_storefront_cache("sabor")

        assert [k for k in tienda.redis.data if "sabor" in k and "otro" not in k] == []
        assert otro in tienda.redis.data   # no toca a los demás negocios

    def test_despues_de_invalidar_se_vuelve_a_leer_la_base(self, tienda, monkeypatch):
        tienda.svc.list_products("sabor", 1, 20)
        monkeypatch.setattr("core.redis_client.get_redis", lambda: tienda.redis)
        invalidate_storefront_cache("sabor")

        tienda.svc.list_products("sabor", 1, 20)

        assert tienda.repo.tenant_lookups == 2
        assert tienda.repo.products.list_calls == 2

    def test_sin_redis_la_tienda_sigue_funcionando(self, tienda, monkeypatch):
        monkeypatch.setattr("storefront.service.get_redis", lambda: None)

        r = tienda.svc.list_products("sabor", 1, 20)

        assert [p.name for p in r.items] == ["Taco", "Flan"]

    def test_redis_caido_no_rompe_la_respuesta(self, tienda, monkeypatch):
        """Un Redis que falla a media petición debe verse como un fallo de caché."""
        monkeypatch.setattr("storefront.service.get_redis", lambda: BrokenRedis())

        r = tienda.svc.list_products("sabor", 1, 20)
        info = tienda.svc.get_storefront("sabor")

        assert r.total == 2
        assert info.business_name == "Sabor"


# ═══════════════════════════════════════════════════════════════════
# PS-14 · Caché HTTP: ETag y 304
# ═══════════════════════════════════════════════════════════════════


def _peticion(if_none_match=None) -> Request:
    headers = [(b"if-none-match", if_none_match.encode())] if if_none_match else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


class TestCacheHttp:
    def test_la_primera_respuesta_trae_etag_y_cache_control(self, tienda):
        modelo = tienda.svc.list_products("sabor", 1, 20)
        respuesta = Response()

        resultado = _with_http_cache(_peticion(), respuesta, modelo)

        assert resultado is modelo
        assert respuesta.headers["etag"].startswith('W/"')
        assert respuesta.headers["cache-control"] == PUBLIC_CACHE_CONTROL

    def test_si_nada_cambio_responde_304_sin_cuerpo(self, tienda):
        modelo = tienda.svc.list_products("sabor", 1, 20)
        primera = Response()
        _with_http_cache(_peticion(), primera, modelo)
        etag = primera.headers["etag"]

        resultado = _with_http_cache(_peticion(etag), Response(), modelo)

        assert resultado.status_code == 304
        assert resultado.body == b""
        assert resultado.headers["etag"] == etag

    def test_si_el_contenido_cambio_manda_el_nuevo(self, tienda):
        antes = tienda.svc.list_products("sabor", 1, 20)
        r1 = Response()
        _with_http_cache(_peticion(), r1, antes)
        etag_viejo = r1.headers["etag"]

        tienda.repo.products.items = [producto("p1", "Taco al pastor")]
        tienda.redis.data.clear()
        despues = tienda.svc.list_products("sabor", 1, 20)

        resultado = _with_http_cache(_peticion(etag_viejo), Response(), despues)

        assert resultado is despues   # el ETag viejo ya no vale: va el cuerpo completo
