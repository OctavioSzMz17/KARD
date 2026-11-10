"""
Recuperar la contraseña por correo (US de acceso).

Aquí el repositorio y Redis son falsos: se prueban las REGLAS (no revelar si el correo existe, enlace de un
solo uso que caduca, topes, sesiones anteriores, bloqueo). Las consultas SQL y el HTTP completo contra la
base real se ven en `tests/integracion/e2e_recuperar.py`.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.dependencies import session_revoked
from core.exceptions import InvalidResetTokenError
from db.database import get_db
from main import app
from notifications import templates
from recovery import routes as routes_module
from recovery import service as service_module
from recovery.models import ForgotPasswordRequest, ResetPasswordRequest
from recovery.repository import Account
from recovery.service import (
    MAX_PER_EMAIL,
    MAX_PER_IP,
    RESET_TTL,
    PasswordRecoveryService,
    reset_link,
    sha256,
)

NOW = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)


# ── Falsos ────────────────────────────────────────────────────────────


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttl = {}

    def incr(self, key):
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    def expire(self, key, seconds):
        self.ttl[key] = seconds

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)

    def exists(self, key):
        return key in self.data


class BrokenRedis(FakeRedis):
    def incr(self, key):
        raise ConnectionError("redis caído")

    def delete(self, *keys):
        raise ConnectionError("redis caído")


class FakeRepo:
    """Misma interfaz que RecoveryRepository, en memoria."""

    def __init__(self):
        self.accounts = {}          # (audience, email) -> Account
        self.resets = []            # PasswordReset
        self.passwords = {}         # (kind, id) -> (hash, changed_at)
        self.consume_result = None  # fuerza el resultado de consume (simula carrera)

    def add_account(self, audience, email, *, kind=None, active=True, name="Eva Pérez", account_id=None):
        kind = kind or ("consumer" if audience == "cliente" else "user")
        acc = Account(kind=kind, id=account_id or f"{kind}-{email}", email=email, name=name, is_active=active)
        self.accounts[(audience, email)] = acc
        return acc

    def find_account(self, audience, email):
        return self.accounts.get((audience, email))

    def get_account(self, kind, account_id):
        for acc in self.accounts.values():
            if acc.kind == kind and acc.id == account_id:
                return acc
        return None

    def set_password(self, kind, account_id, password_hash, now):
        self.passwords[(kind, account_id)] = (password_hash, now)

    def add(self, reset):
        self.resets.append(reset)

    def get_by_hash(self, token_hash):
        return next((r for r in self.resets if r.token_hash == token_hash), None)

    def invalidate_open(self, kind, account_id, now):
        n = 0
        for r in self.resets:
            if r.account_kind == kind and r.account_id == account_id and r.used_at is None:
                r.used_at = now
                n += 1
        return n

    def consume(self, reset_id, now):
        if self.consume_result is not None:
            return self.consume_result
        for r in self.resets:
            if r.id == reset_id and r.used_at is None and r.expires_at > now:
                r.used_at = now
                return True
        return False


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


@pytest.fixture
def mundo(monkeypatch):
    """Servicio con repositorio y Redis falsos, sin correo real ni hash lento."""
    monkeypatch.setattr(service_module, "hash_password", lambda plain: f"hash::{plain}")
    monkeypatch.setattr(service_module, "log_auth_event", lambda **kw: None)
    repo, redis, db = FakeRepo(), FakeRedis(), FakeDb()
    svc = PasswordRecoveryService(db, redis_client=redis)
    svc.repo = repo
    return SimpleNamespace(svc=svc, repo=repo, redis=redis, db=db)


def token_de(resp):
    return resp.link.split("#token=")[1]


# ── Pedir el enlace ───────────────────────────────────────────────────


class TestPedirEnlace:
    def test_un_correo_que_no_existe_no_genera_nada(self, mundo):
        assert mundo.svc.request("nadie@kard.mx", "negocio", "1.1.1.1") is None
        assert mundo.repo.resets == []

    def test_una_cuenta_dada_de_baja_no_recibe_enlace(self, mundo):
        mundo.repo.add_account("negocio", "baja@kard.mx", active=False)
        assert mundo.svc.request("baja@kard.mx", "negocio", None) is None
        assert mundo.repo.resets == []

    def test_una_cuenta_existente_recibe_su_enlace(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        out = mundo.svc.request("eva@kard.mx", "negocio", "1.1.1.1")

        assert out.to == "eva@kard.mx"
        assert out.name == "Eva Pérez"
        assert "/restablecer#token=" in out.link
        assert mundo.db.commits == 1

    def test_el_correo_se_normaliza(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        out = mundo.svc.request("  EVA@Kard.MX ", "negocio", None)
        assert out is not None and out.to == "eva@kard.mx"

    def test_solo_se_guarda_la_huella_del_enlace_nunca_el_enlace(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        token = token_de(mundo.svc.request("eva@kard.mx", "negocio", None))

        guardado = mundo.repo.resets[0]
        assert guardado.token_hash == sha256(token)
        assert token not in guardado.token_hash
        assert token not in repr(vars(guardado))

    def test_el_enlace_va_en_el_fragmento_no_en_la_consulta(self, mundo):
        """El `#` no viaja al servidor ni en el Referer: no queda en bitácoras."""
        link = reset_link("abc")
        assert "#token=abc" in link and "?" not in link

    def test_el_enlace_dura_una_hora(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        mundo.svc.request("eva@kard.mx", "negocio", None)
        r = mundo.repo.resets[0]
        assert RESET_TTL == timedelta(hours=1)
        assert r.expires_at - r.created_at == RESET_TTL

    def test_cada_enlace_es_distinto_y_largo(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        a = token_de(mundo.svc.request("eva@kard.mx", "negocio", None))
        b = token_de(mundo.svc.request("eva@kard.mx", "negocio", None))
        assert a != b
        assert len(a) >= 40            # 256 bits

    def test_pedir_otro_anula_el_anterior(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        mundo.svc.request("eva@kard.mx", "negocio", None)
        mundo.svc.request("eva@kard.mx", "negocio", None)

        primero, segundo = mundo.repo.resets
        assert primero.used_at is not None      # anulado
        assert segundo.used_at is None          # el último sigue vivo

    def test_negocio_y_cliente_son_mundos_separados(self, mundo):
        mundo.repo.add_account("cliente", "ana@gmail.com")
        assert mundo.svc.request("ana@gmail.com", "negocio", None) is None
        assert mundo.svc.request("ana@gmail.com", "cliente", None) is not None

    def test_el_enlace_de_un_cliente_apunta_a_la_cuenta_cliente(self, mundo):
        mundo.repo.add_account("cliente", "ana@gmail.com")
        mundo.svc.request("ana@gmail.com", "cliente", None)
        assert mundo.repo.resets[0].account_kind == "consumer"

    def test_se_guarda_desde_que_direccion_se_pidio(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        mundo.svc.request("eva@kard.mx", "negocio", "9.9.9.9")
        assert mundo.repo.resets[0].requested_ip == "9.9.9.9"


class TestTopes:
    def test_por_correo_se_permiten_solo_unos_cuantos_por_hora(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        resultados = [mundo.svc.request("eva@kard.mx", "negocio", None) for _ in range(MAX_PER_EMAIL + 2)]

        assert [r is not None for r in resultados] == [True] * MAX_PER_EMAIL + [False, False]
        assert len(mundo.repo.resets) == MAX_PER_EMAIL

    def test_el_tope_por_correo_cuenta_aunque_la_cuenta_no_exista(self, mundo):
        """Si solo contara para cuentas reales, el tope delataría cuáles correos existen."""
        for _ in range(MAX_PER_EMAIL + 1):
            mundo.svc.request("fantasma@kard.mx", "negocio", None)
        assert mundo.redis.data["auth:forgot:email:negocio:fantasma@kard.mx"] == MAX_PER_EMAIL + 1

    def test_por_direccion_tambien_hay_tope(self, mundo):
        for i in range(MAX_PER_IP + 3):
            mundo.repo.add_account("negocio", f"u{i}@kard.mx")
        resultados = [mundo.svc.request(f"u{i}@kard.mx", "negocio", "2.2.2.2") for i in range(MAX_PER_IP + 3)]

        assert sum(r is not None for r in resultados) == MAX_PER_IP

    def test_la_cuenta_regresiva_del_tope_empieza_en_la_primera_solicitud(self, mundo):
        mundo.svc.request("eva@kard.mx", "negocio", "3.3.3.3")
        assert mundo.redis.ttl["auth:forgot:email:negocio:eva@kard.mx"] == 3600
        assert mundo.redis.ttl["auth:forgot:ip:3.3.3.3"] == 3600

    def test_otra_direccion_no_comparte_el_tope(self, mundo):
        mundo.repo.add_account("negocio", "eva@kard.mx")
        for _ in range(MAX_PER_EMAIL):
            mundo.svc.request("eva@kard.mx", "negocio", "4.4.4.4")
        assert mundo.svc.request("otra@kard.mx", "negocio", "4.4.4.4") is None  # no existe, pero no por el tope
        assert mundo.redis.data["auth:forgot:ip:4.4.4.4"] == MAX_PER_EMAIL + 1

    def test_si_redis_falla_se_deja_pasar(self, monkeypatch):
        monkeypatch.setattr(service_module, "log_auth_event", lambda **kw: None)
        repo = FakeRepo()
        repo.add_account("negocio", "eva@kard.mx")
        svc = PasswordRecoveryService(FakeDb(), redis_client=BrokenRedis())
        svc.repo = repo
        assert svc.request("eva@kard.mx", "negocio", "1.1.1.1") is not None


# ── Usar el enlace ────────────────────────────────────────────────────


def pedir(mundo, audience="negocio", email="eva@kard.mx", **kw):
    mundo.repo.add_account(audience, email, **kw)
    return token_de(mundo.svc.request(email, audience, None))


class TestUsarEnlace:
    def test_con_un_enlace_valido_cambia_la_contrasena(self, mundo):
        token = pedir(mundo)
        done = mundo.svc.reset(token, "NuevaClave9", "1.1.1.1")

        assert done.to == "eva@kard.mx"
        (clave, cuando), = mundo.repo.passwords.values()
        assert clave == "hash::NuevaClave9"
        assert cuando is not None

    def test_la_contrasena_se_guarda_en_la_cuenta_correcta(self, mundo):
        token = pedir(mundo, "cliente", "ana@gmail.com")
        mundo.svc.reset(token, "NuevaClave9", None)
        assert list(mundo.repo.passwords) == [("consumer", "consumer-ana@gmail.com")]

    def test_el_enlace_sirve_una_sola_vez(self, mundo):
        token = pedir(mundo)
        mundo.svc.reset(token, "NuevaClave9", None)
        with pytest.raises(InvalidResetTokenError):
            mundo.svc.reset(token, "OtraClave99", None)
        assert mundo.repo.passwords[("user", "user-eva@kard.mx")][0] == "hash::NuevaClave9"

    def test_un_enlace_que_no_existe_se_rechaza(self, mundo):
        with pytest.raises(InvalidResetTokenError):
            mundo.svc.reset("x" * 43, "NuevaClave9", None)
        assert mundo.repo.passwords == {}

    def test_un_enlace_caducado_se_rechaza(self, mundo):
        token = pedir(mundo)
        mundo.repo.resets[0].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        with pytest.raises(InvalidResetTokenError):
            mundo.svc.reset(token, "NuevaClave9", None)
        assert mundo.repo.passwords == {}

    def test_un_enlace_anulado_por_otro_mas_nuevo_se_rechaza(self, mundo):
        viejo = pedir(mundo)
        mundo.svc.request("eva@kard.mx", "negocio", None)
        with pytest.raises(InvalidResetTokenError):
            mundo.svc.reset(viejo, "NuevaClave9", None)

    def test_si_la_cuenta_se_dio_de_baja_el_enlace_ya_no_sirve(self, mundo):
        token = pedir(mundo)
        mundo.repo.accounts[("negocio", "eva@kard.mx")] = Account(
            kind="user", id="user-eva@kard.mx", email="eva@kard.mx", name="Eva", is_active=False)
        with pytest.raises(InvalidResetTokenError):
            mundo.svc.reset(token, "NuevaClave9", None)
        assert mundo.repo.passwords == {}

    def test_si_otra_peticion_gasto_el_enlace_primero_no_se_cambia_nada(self, mundo):
        """Dos clics a la vez: solo uno gana el UPDATE atómico; el otro no debe tocar la contraseña."""
        token = pedir(mundo)
        mundo.repo.consume_result = False
        with pytest.raises(InvalidResetTokenError):
            mundo.svc.reset(token, "NuevaClave9", None)
        assert mundo.repo.passwords == {}

    def test_todos_los_fallos_dan_el_mismo_error(self, mundo):
        """No se le dice a quien adivina si el enlace no existe, caducó o ya se usó."""
        usado = pedir(mundo)
        mundo.svc.reset(usado, "NuevaClave9", None)
        mensajes = set()
        for tok in (usado, "z" * 43):
            with pytest.raises(InvalidResetTokenError) as e:
                mundo.svc.reset(tok, "NuevaClave9", None)
            mensajes.add(str(e.value))
        assert len(mensajes) == 1

    def test_restablecer_anula_los_demas_enlaces_abiertos(self, mundo):
        token = pedir(mundo)
        # Un enlace "hermano" abierto (p. ej. pedido desde otro lado antes de usar este).
        mundo.repo.add(SimpleNamespace(
            id="otro", account_kind="user", account_id="user-eva@kard.mx", token_hash="h2",
            created_at=NOW, expires_at=NOW + timedelta(days=1), used_at=None, requested_ip=None))
        mundo.svc.reset(token, "NuevaClave9", None)
        assert all(r.used_at is not None for r in mundo.repo.resets)

    def test_restablecer_quita_el_bloqueo_por_intentos_fallidos(self, mundo):
        token = pedir(mundo)
        mundo.redis.data["auth:failed:eva@kard.mx"] = 5
        mundo.redis.data["auth:lockout:eva@kard.mx"] = 1
        mundo.svc.reset(token, "NuevaClave9", None)
        assert "auth:failed:eva@kard.mx" not in mundo.redis.data
        assert "auth:lockout:eva@kard.mx" not in mundo.redis.data

    def test_si_redis_falla_al_quitar_el_bloqueo_el_cambio_igual_se_hace(self, monkeypatch):
        monkeypatch.setattr(service_module, "hash_password", lambda p: f"hash::{p}")
        monkeypatch.setattr(service_module, "log_auth_event", lambda **kw: None)
        repo = FakeRepo()
        repo.add_account("negocio", "eva@kard.mx")
        svc = PasswordRecoveryService(FakeDb(), redis_client=BrokenRedis())
        svc.repo = repo
        token = token_de(svc.request("eva@kard.mx", "negocio", None))
        svc.reset(token, "NuevaClave9", None)
        assert repo.passwords


# ── Sesiones anteriores ───────────────────────────────────────────────


class TestSesionesAnteriores:
    CAMBIO = datetime(2026, 11, 6, 12, 0, 0, 700000, tzinfo=timezone.utc)

    def ts(self, delta):
        return int(self.CAMBIO.timestamp()) + delta

    def test_si_nunca_restablecio_nada_se_respeta(self):
        assert session_revoked({"iat": 1}, None) is False

    def test_una_sesion_anterior_al_cambio_deja_de_valer(self):
        assert session_revoked({"iat": self.ts(-60)}, self.CAMBIO) is True

    def test_una_sesion_posterior_al_cambio_vale(self):
        assert session_revoked({"iat": self.ts(+5)}, self.CAMBIO) is False

    def test_la_sesion_del_mismo_segundo_vale(self):
        """Es la que abre quien acaba de poner su contraseña nueva."""
        assert session_revoked({"iat": self.ts(0)}, self.CAMBIO) is False

    def test_un_segundo_antes_ya_no_vale(self):
        assert session_revoked({"iat": self.ts(-1)}, self.CAMBIO) is True

    def test_sin_fecha_de_emision_no_se_puede_probar_y_no_vale(self):
        assert session_revoked({}, self.CAMBIO) is True


# ── Validación de lo que llega ────────────────────────────────────────


class TestValidacion:
    TOKEN = "a" * 43

    def test_la_contrasena_nueva_sigue_la_misma_regla_del_registro(self):
        ResetPasswordRequest(token=self.TOKEN, password="Clave1234")
        for mala in ("corta1", "sinnumeros", "12345678"):
            with pytest.raises(ValidationError):
                ResetPasswordRequest(token=self.TOKEN, password=mala)

    def test_hay_un_maximo_de_largo(self):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(token=self.TOKEN, password="a1" * 70)

    @pytest.mark.parametrize("token", ["", "corto", "con espacios " * 4, "a/b" * 10, "x" * 300])
    def test_el_enlace_debe_tener_forma_de_enlace(self, token):
        with pytest.raises(ValidationError):
            ResetPasswordRequest(token=token, password="Clave1234")

    def test_correo_invalido(self):
        with pytest.raises(ValidationError):
            ForgotPasswordRequest(email="no-es-correo")

    def test_el_lado_por_omision_es_negocio_y_solo_hay_dos(self):
        assert ForgotPasswordRequest(email="a@b.mx").audience == "negocio"
        with pytest.raises(ValidationError):
            ForgotPasswordRequest(email="a@b.mx", audience="admin")


# ── HTTP ──────────────────────────────────────────────────────────────


class ServicioFalso:
    pedidos = []
    por_pedir = None
    por_restablecer = None

    def __init__(self, db):
        pass

    def request(self, email, audience, ip):
        ServicioFalso.pedidos.append((email, audience, ip))
        return ServicioFalso.por_pedir

    def reset(self, token, password, ip):
        if ServicioFalso.por_restablecer is None:
            raise InvalidResetTokenError()
        return ServicioFalso.por_restablecer


@pytest.fixture
def http(monkeypatch):
    ServicioFalso.pedidos = []
    ServicioFalso.por_pedir = None
    ServicioFalso.por_restablecer = None
    enviados = []
    monkeypatch.setattr(routes_module, "PasswordRecoveryService", ServicioFalso)
    monkeypatch.setattr(routes_module.NotificationService, "password_reset",
                        staticmethod(lambda **kw: enviados.append(("reset", kw))))
    monkeypatch.setattr(routes_module.NotificationService, "password_changed",
                        staticmethod(lambda **kw: enviados.append(("changed", kw))))
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    try:
        yield SimpleNamespace(client=TestClient(app, raise_server_exceptions=False), enviados=enviados)
    finally:
        app.dependency_overrides.pop(get_db, None)


FORGOT = "/api/v1/auth/forgot-password"
RESET = "/api/v1/auth/reset-password"


class TestRutas:
    def test_pedir_enlace_responde_lo_mismo_exista_o_no(self, http):
        sin_cuenta = http.client.post(FORGOT, json={"email": "nadie@kard.mx"})

        ServicioFalso.por_pedir = service_module.ResetEmail(to="eva@kard.mx", name="Eva", link="http://x/restablecer#token=t")
        con_cuenta = http.client.post(FORGOT, json={"email": "eva@kard.mx"})

        assert sin_cuenta.status_code == con_cuenta.status_code == 202
        assert sin_cuenta.json() == con_cuenta.json()

    def test_el_correo_se_manda_solo_si_hay_cuenta(self, http):
        http.client.post(FORGOT, json={"email": "nadie@kard.mx"})
        assert http.enviados == []

        ServicioFalso.por_pedir = service_module.ResetEmail(to="eva@kard.mx", name="Eva", link="http://x/restablecer#token=t")
        http.client.post(FORGOT, json={"email": "eva@kard.mx"})
        assert http.enviados == [("reset", {"email": "eva@kard.mx", "name": "Eva", "link": "http://x/restablecer#token=t"})]

    def test_la_respuesta_no_contiene_el_enlace(self, http):
        ServicioFalso.por_pedir = service_module.ResetEmail(to="eva@kard.mx", name="Eva", link="http://x/restablecer#token=SECRETO")
        r = http.client.post(FORGOT, json={"email": "eva@kard.mx"})
        assert "SECRETO" not in r.text and "eva@kard.mx" not in r.text

    def test_pasa_el_lado_y_la_direccion(self, http):
        http.client.post(FORGOT, json={"email": "ana@gmail.com", "audience": "cliente"},
                         headers={"X-Forwarded-For": "8.8.8.8, 10.0.0.1"})
        assert ServicioFalso.pedidos == [("ana@gmail.com", "cliente", "8.8.8.8")]

    def test_correo_invalido_es_422(self, http):
        assert http.client.post(FORGOT, json={"email": "no"}).status_code == 422

    def test_restablecer_ok_avisa_por_correo_que_cambio(self, http):
        ServicioFalso.por_restablecer = service_module.ResetDone(to="eva@kard.mx", name="Eva")
        r = http.client.post(RESET, json={"token": "a" * 43, "password": "Clave1234"})

        assert r.status_code == 200
        assert "cambió" in r.json()["message"]
        assert http.enviados == [("changed", {"email": "eva@kard.mx", "name": "Eva"})]

    def test_enlace_invalido_es_400_con_codigo(self, http):
        r = http.client.post(RESET, json={"token": "a" * 43, "password": "Clave1234"})
        assert r.status_code == 400
        assert r.json()["code"] == "INVALID_RESET_TOKEN"
        assert http.enviados == []

    def test_contrasena_debil_es_422_y_no_gasta_el_enlace(self, http):
        ServicioFalso.por_restablecer = service_module.ResetDone(to="eva@kard.mx", name="Eva")
        r = http.client.post(RESET, json={"token": "a" * 43, "password": "sinnumeros"})
        assert r.status_code == 422
        assert http.enviados == []

    def test_las_rutas_son_publicas(self, http):
        """Las usa quien no puede iniciar sesión: ninguna pide autenticación."""
        for ruta, cuerpo in ((FORGOT, {"email": "a@b.mx"}), (RESET, {"token": "a" * 43, "password": "Clave1234"})):
            assert http.client.post(ruta, json=cuerpo).status_code != 401


# ── Correos ───────────────────────────────────────────────────────────


class TestCorreos:
    def test_el_correo_lleva_el_enlace_y_dice_cuanto_dura(self):
        tpl = templates.password_reset(name="Eva", link="https://kard.mx/restablecer#token=abc")
        assert "https://kard.mx/restablecer#token=abc" in tpl["html"]
        assert "1 hora" in tpl["html"] and "una sola vez" in tpl["html"]
        assert "contraseña" in tpl["subject"].lower()

    def test_el_nombre_no_puede_meter_html(self):
        tpl = templates.password_reset(name='<script>alert(1)</script>', link="https://kard.mx/x")
        assert "<script>" not in tpl["html"]
        assert "&lt;script&gt;" in tpl["html"]

    def test_el_enlace_no_puede_romper_el_atributo(self):
        tpl = templates.password_reset(name=None, link='https://kard.mx/x"onmouseover="alert(1)')
        assert '"onmouseover="' not in tpl["html"]

    def test_sin_nombre_saluda_igual(self):
        assert "Hola" in templates.password_reset(name=None, link="https://kard.mx/x")["html"]
        assert "Hola" in templates.password_changed(name=None)["html"]

    def test_el_aviso_de_cambio_no_lleva_enlaces_de_acceso(self):
        html = templates.password_changed(name="Eva")["html"]
        assert "href" not in html and "token" not in html

    def test_en_desarrollo_sin_proveedor_el_enlace_sale_en_la_consola(self, mundo, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(service_module.settings, "RESEND_API_KEY", "")
        monkeypatch.setattr(service_module.settings, "ENVIRONMENT", "development")
        mundo.repo.add_account("negocio", "eva@kard.mx")
        with caplog.at_level(logging.INFO, logger="kard.recovery"):
            out = mundo.svc.request("eva@kard.mx", "negocio", None)
        assert out.link in caplog.text

    def test_en_produccion_el_enlace_nunca_sale_en_la_consola(self, mundo, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(service_module.settings, "RESEND_API_KEY", "")
        monkeypatch.setattr(service_module.settings, "ENVIRONMENT", "production")
        mundo.repo.add_account("negocio", "eva@kard.mx")
        with caplog.at_level(logging.DEBUG, logger="kard.recovery"):
            out = mundo.svc.request("eva@kard.mx", "negocio", None)
        assert token_de(out) not in caplog.text


# ── Las dependencias de sesión aplican la revocación ──────────────────


class FakeQueryDb:
    """db.query(...).filter(...).first() → la fila indicada."""

    def __init__(self, row):
        self.row = row

    def query(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        return self.row


class TestSesionesEnLasDependencias:
    def _usuario(self, changed_at):
        return SimpleNamespace(
            id="u1", tenant_id="t1", role="employee", email="eva@kard.mx", is_active=True,
            job_title=None, full_name="Eva Pérez", employee_code=None, password_changed_at=changed_at,
        )

    def _token_usuario(self):
        from core.security import create_token
        return create_token({"user_id": "u1", "tenant_id": "t1", "business_type": "restaurant", "role": "employee"})

    def _token_cliente(self):
        from core.security import create_token
        return create_token({"consumer_id": "c1", "type": "consumer"})

    def test_el_usuario_sin_cambios_de_contrasena_entra(self):
        from core.dependencies import get_current_user
        assert get_current_user(token=self._token_usuario(), db=FakeQueryDb(self._usuario(None))).user_id == "u1"

    def test_el_usuario_con_sesion_anterior_al_cambio_no_entra(self):
        from fastapi import HTTPException
        from core.dependencies import get_current_user
        token = self._token_usuario()
        despues = datetime.now(timezone.utc) + timedelta(seconds=30)
        with pytest.raises(HTTPException) as e:
            get_current_user(token=token, db=FakeQueryDb(self._usuario(despues)))
        assert e.value.status_code == 401

    def test_el_usuario_con_sesion_posterior_al_cambio_entra(self):
        from core.dependencies import get_current_user
        antes = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert get_current_user(token=self._token_usuario(), db=FakeQueryDb(self._usuario(antes))).user_id == "u1"

    def _cliente(self, changed_at):
        return SimpleNamespace(id="c1", email="ana@gmail.com", is_active=True, password_changed_at=changed_at)

    def test_el_cliente_con_sesion_anterior_al_cambio_no_entra(self):
        from fastapi import HTTPException
        from core.dependencies import get_current_consumer
        token = self._token_cliente()
        despues = datetime.now(timezone.utc) + timedelta(seconds=30)
        with pytest.raises(HTTPException) as e:
            get_current_consumer(token=token, db=FakeQueryDb(self._cliente(despues)))
        assert e.value.status_code == 401

    def test_el_cliente_sin_cambios_o_con_sesion_posterior_entra(self):
        from core.dependencies import get_current_consumer
        assert get_current_consumer(token=self._token_cliente(), db=FakeQueryDb(self._cliente(None))).consumer_id == "c1"
        antes = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert get_current_consumer(token=self._token_cliente(), db=FakeQueryDb(self._cliente(antes))).consumer_id == "c1"
