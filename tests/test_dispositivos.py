"""
Pruebas de los equipos autorizados y el acceso por QR.

El diseño parte de que el QR lo puede fotografiar cualquiera. Lo que estas
pruebas protegen:

  * Fotografiar el QR no sirve: el resultado solo se entrega a quien tiene el
    secreto de la computadora que lo pidió, y una sola vez.
  * Autorizar un equipo exige teclear el código que se ve en SU pantalla, con
    tres intentos.
  * Un QR de otro negocio no existe para ti.
  * Cobrar NO depende del equipo: cualquier puesto con permiso de cobrar lo hace desde su
    propio celular o computadora. El equipo autorizado solo existe para el modo caja.
  * El token del equipo no queda en la base: solo su hash.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from auth.service import AuthService
from core import permissions as P
from core.dependencies import CurrentUser, get_current_user
from core.exceptions import (
    ChallengeExpiredError,
    ChallengeNotFoundError,
    InvalidCredentialsError,
    PermissionDeniedError,
    TooManyChallengesError,
    WrongVerificationCodeError,
)
from db.models import AuthChallenge, Device
from devices import service as service_module
from devices.models import ApproveRequest, DeviceLoginRequest
from devices.service import (
    DeviceService,
    clean_device_name,
    describe_user_agent,
    sha256,
)
from main import app
from tests.conftest import FakeSession

AHORA = datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────────
# Fábricas y repositorios en memoria
# ──────────────────────────────────────────────────────────────────────


def usuario(position: str, tenant_id: str = "t_aaa", user_id: str | None = None) -> CurrentUser:
    owner = position == P.OWNER
    return CurrentUser(
        user_id=user_id or f"u_{position}", tenant_id=tenant_id, business_type="restaurant",
        role="business_admin" if owner else "staff", email=f"{position}@example.com",
        job_title=P.title_from_position(position),
    )


class FakeDeviceRepository:
    def __init__(self, devices=None):
        self.devices = list(devices or [])

    def create(self, device):
        self.devices.append(device)
        return device

    def get_valid_by_token_hash(self, token_hash, now):
        return next((d for d in self.devices if d.token_hash == token_hash and d.revoked_at is None and d.expires_at > now), None)

    def get_by_id(self, tenant_id, device_id):
        return next((d for d in self.devices if d.tenant_id == tenant_id and d.id == device_id), None)

    def list_active(self, tenant_id, now):
        return [(d, "quien@example.com") for d in self.devices if d.tenant_id == tenant_id and d.revoked_at is None and d.expires_at > now]

    def revoke(self, device, now):
        device.revoked_at = now


class FakeChallengeRepository:
    def __init__(self):
        self.items = {}

    def create(self, challenge):
        self.items[challenge.id] = challenge
        return challenge

    def get(self, challenge_id, lock=False):
        return self.items.get(challenge_id)

    def save(self, challenge):
        self.items[challenge.id] = challenge

    def delete_expired(self, before):
        return 0


class FakeUserRepository:
    def __init__(self, users):
        self.users = users

    def get_by_email(self, email):
        return next((u for u in self.users if u.email == email), None)

    def get_by_id(self, user_id, tenant_id):
        return next((u for u in self.users if u.id == user_id and u.tenant_id == tenant_id), None)

    def get_by_employee_code(self, tenant_id, code):
        return next((u for u in self.users if u.tenant_id == tenant_id and u.employee_code == code), None)

    def record_login(self, user):
        user.last_login = AHORA


class FakeTenantRepository:
    def get_by_id(self, tenant_id):
        return SimpleNamespace(id=tenant_id, business_type="restaurant", is_active=True)


def fila(user_id, position, tenant_id="t_aaa", code="0001", email=None, active=True, password="hash:Segura123"):
    return SimpleNamespace(
        id=user_id, tenant_id=tenant_id, email=email or f"{user_id}@example.com",
        role="business_admin" if position == P.OWNER else "staff", job_title=P.title_from_position(position),
        full_name=f"Nombre {user_id}", employee_code=code, is_active=active, last_login=None, password_hash=password,
    )


def equipo_autorizado(device_id="dv_1", tenant_id="t_aaa", token="token-secreto", name="Caja 1", ttl=timedelta(hours=10)):
    return Device(id=device_id, tenant_id=tenant_id, name=name, token_hash=sha256(token), authorized_by="u_gerente",
                  user_agent=None, created_at=AHORA, expires_at=AHORA + ttl)


@pytest.fixture
def servicio(monkeypatch):
    """Un DeviceService con todo en memoria: sin base, sin Redis, sin bitácora y sin bcrypt."""
    llamadas = []
    monkeypatch.setattr(service_module, "log_operation", lambda **kw: llamadas.append(kw))
    monkeypatch.setattr(service_module, "log_auth_event", lambda **kw: None)
    monkeypatch.setattr(service_module, "get_redis", lambda: None)
    monkeypatch.setattr(service_module, "verify_password", lambda plain, hashed: hashed == f"hash:{plain}")
    monkeypatch.setattr(AuthService, "_issue_token", staticmethod(lambda user, tenant: f"jwt-de-{user.id}"))

    def _build(users=None, devices=None):
        svc = DeviceService.__new__(DeviceService)
        svc.db = FakeSession()
        svc.devices = FakeDeviceRepository(devices)
        svc.challenges = FakeChallengeRepository()
        svc.users = FakeUserRepository(users or [])
        svc.tenants = FakeTenantRepository()
        svc.audit = llamadas
        return svc

    return _build


SECRETO = "s" * 64
HASH = sha256(SECRETO)


def pedir_enroll(svc):
    return svc.create_enroll_challenge(HASH, "Mozilla/5.0 (Windows NT 10.0) Chrome/120", "1.2.3.4")


# ──────────────────────────────────────────────────────────────────────
# Crear el QR
# ──────────────────────────────────────────────────────────────────────


class TestCrearElQR:
    def test_guarda_el_hash_del_secreto_nunca_el_secreto(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        guardado = svc.challenges.items[c.challenge_id]
        assert guardado.secret_hash == HASH
        assert SECRETO not in str(vars(guardado))

    def test_el_codigo_es_de_cuatro_digitos_y_caduca_a_los_pocos_minutos(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        assert len(c.verification_code) == 4 and c.verification_code.isdigit()
        assert 100 <= c.expires_in <= 300
        assert c.expires_at > AHORA

    def test_un_enroll_nace_sin_negocio(self, servicio):
        svc = servicio()
        assert svc.challenges.items[pedir_enroll(svc).challenge_id].tenant_id is None

    def test_un_unlock_nace_atado_al_equipo_y_a_su_negocio(self, servicio):
        dev = equipo_autorizado()
        svc = servicio(devices=[dev])
        c = svc.create_unlock_challenge(dev, HASH, None, "1.2.3.4")
        guardado = svc.challenges.items[c.challenge_id]
        assert (guardado.kind, guardado.tenant_id, guardado.device_id) == ("unlock", "t_aaa", "dv_1")

    def test_los_identificadores_no_se_repiten_ni_se_pueden_adivinar(self, servicio):
        svc = servicio()
        ids = {pedir_enroll(svc).challenge_id for _ in range(200)}
        assert len(ids) == 200
        assert all(len(i) >= 20 for i in ids)

    def test_pedir_demasiados_se_limita_por_direccion(self, servicio, monkeypatch):
        class RedisFalso:
            n = 0
            def incr(self, key):
                self.n += 1
                return self.n
            def expire(self, *a): pass
        redis = RedisFalso()      # una sola instancia: el contador tiene que acumularse
        monkeypatch.setattr(service_module, "get_redis", lambda: redis)
        svc = servicio()
        for _ in range(service_module.MAX_CHALLENGES_PER_IP):
            pedir_enroll(svc)
        with pytest.raises(TooManyChallengesError):
            pedir_enroll(svc)


# ──────────────────────────────────────────────────────────────────────
# Autorizar un equipo
# ──────────────────────────────────────────────────────────────────────


def aprobar(svc, c, quien, **kw):
    kw.setdefault("code", c.verification_code)
    kw.setdefault("device_name", "Caja 1")
    return svc.approve(quien, c.challenge_id, ApproveRequest(**kw))


class TestAutorizarUnEquipo:
    @pytest.mark.parametrize("puesto", [P.OWNER, P.MANAGER, P.ASSISTANT_MANAGER])
    def test_la_gerencia_autoriza(self, servicio, puesto):
        svc = servicio()
        c = pedir_enroll(svc)
        assert aprobar(svc, c, usuario(puesto)).status == "approved"
        assert svc.challenges.items[c.challenge_id].tenant_id == "t_aaa"

    @pytest.mark.parametrize("puesto", [P.AREA_LEAD, P.EMPLOYEE])
    def test_un_empleado_no_autoriza_equipos(self, servicio, puesto):
        svc = servicio()
        c = pedir_enroll(svc)
        with pytest.raises(PermissionDeniedError):
            aprobar(svc, c, usuario(puesto))
        assert svc.challenges.items[c.challenge_id].status == "pending"

    def test_el_negocio_lo_pone_quien_aprueba(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        aprobar(svc, c, usuario(P.MANAGER, tenant_id="t_bbb"))
        assert svc.challenges.items[c.challenge_id].tenant_id == "t_bbb"

    def test_exige_el_codigo_de_la_pantalla(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        mal = "0000" if c.verification_code != "0000" else "1111"
        with pytest.raises(WrongVerificationCodeError):
            aprobar(svc, c, usuario(P.MANAGER), code=mal)
        assert svc.challenges.items[c.challenge_id].status == "pending"

    def test_sin_codigo_tampoco(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        with pytest.raises(WrongVerificationCodeError):
            svc.approve(usuario(P.MANAGER), c.challenge_id, ApproveRequest(device_name="Caja 1"))

    def test_tres_codigos_equivocados_anulan_el_qr(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        mal = "0000" if c.verification_code != "0000" else "1111"
        for _ in range(3):
            with pytest.raises(WrongVerificationCodeError):
                aprobar(svc, c, usuario(P.MANAGER), code=mal)
        assert svc.challenges.items[c.challenge_id].status == "denied"
        # Ni siquiera con el código correcto: ya está anulado.
        with pytest.raises(ChallengeNotFoundError):
            aprobar(svc, c, usuario(P.MANAGER))

    def test_los_intentos_se_guardan_aunque_la_peticion_falle(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        commits = svc.db.commits
        with pytest.raises(WrongVerificationCodeError):
            aprobar(svc, c, usuario(P.MANAGER), code="9999" if c.verification_code != "9999" else "8888")
        assert svc.db.commits == commits + 1

    def test_un_qr_caducado_no_se_aprueba(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        svc.challenges.items[c.challenge_id].expires_at = AHORA - timedelta(seconds=1)
        with pytest.raises(ChallengeExpiredError):
            aprobar(svc, c, usuario(P.MANAGER))

    def test_un_qr_ya_aprobado_no_se_aprueba_otra_vez(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        aprobar(svc, c, usuario(P.MANAGER))
        with pytest.raises(ChallengeNotFoundError):
            aprobar(svc, c, usuario(P.OWNER))

    def test_un_qr_que_no_existe(self, servicio):
        with pytest.raises(ChallengeNotFoundError):
            servicio().approve(usuario(P.MANAGER), "no-existe", ApproveRequest(code="1234"))

    def test_el_nombre_se_limpia_y_tiene_un_valor_por_omision(self):
        assert clean_device_name("  Caja   1 ") == "Caja 1"
        assert clean_device_name("   ") == "Caja"
        assert clean_device_name(None) == "Caja"
        assert len(clean_device_name("x" * 200)) == 60

    def test_abre_la_caja_solo_si_se_pide(self, servicio, monkeypatch):
        abiertas = []
        svc = servicio()
        monkeypatch.setattr(svc, "_open_cash", lambda u, f: abiertas.append(f) or True)
        c = pedir_enroll(svc)
        assert aprobar(svc, c, usuario(P.MANAGER)).cash_opened is False
        assert abiertas == []

        c2 = pedir_enroll(svc)
        r = aprobar(svc, c2, usuario(P.MANAGER), open_float="500")
        assert r.cash_opened is True and str(abiertas[0]) == "500"

    def test_queda_en_la_bitacora(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        aprobar(svc, c, usuario(P.MANAGER))
        assert svc.audit[-1]["action"] == "APPROVE_ENROLL"


# ──────────────────────────────────────────────────────────────────────
# Recoger el resultado (la computadora que mostró el QR)
# ──────────────────────────────────────────────────────────────────────


class TestReclamar:
    def test_mientras_nadie_aprueba_sigue_pendiente(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        assert svc.claim(c.challenge_id, SECRETO).status == "pending"

    def test_quien_fotografio_el_qr_no_puede_reclamar(self, servicio):
        """Conoce el identificador del QR pero no el secreto de la computadora."""
        svc = servicio()
        c = pedir_enroll(svc)
        aprobar(svc, c, usuario(P.MANAGER))
        with pytest.raises(ChallengeNotFoundError):
            svc.claim(c.challenge_id, "x" * 64)
        assert svc.devices.devices == []

    def test_un_secreto_equivocado_es_igual_a_un_qr_inexistente(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        for intento in ("x" * 64, "y" * 64):
            with pytest.raises(ChallengeNotFoundError):
                svc.claim(c.challenge_id, intento)

    def test_entrega_el_token_del_equipo_y_en_la_base_solo_queda_su_hash(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        aprobar(svc, c, usuario(P.MANAGER), device_name="Caja 2")
        r = svc.claim(c.challenge_id, SECRETO)

        assert r.status == "approved" and r.device_token and r.device_name == "Caja 2"
        guardado = svc.devices.devices[0]
        assert guardado.token_hash == sha256(r.device_token)
        assert r.device_token not in str(vars(guardado))

    def test_el_token_se_entrega_una_sola_vez(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        aprobar(svc, c, usuario(P.MANAGER))
        svc.claim(c.challenge_id, SECRETO)
        with pytest.raises(ChallengeNotFoundError):
            svc.claim(c.challenge_id, SECRETO)
        assert len(svc.devices.devices) == 1

    def test_el_equipo_es_del_negocio_de_quien_aprobo_y_dura_lo_configurado(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        aprobar(svc, c, usuario(P.MANAGER, tenant_id="t_bbb", user_id="u_ger_b"))
        r = svc.claim(c.challenge_id, SECRETO)
        d = svc.devices.devices[0]
        assert (d.tenant_id, d.authorized_by) == ("t_bbb", "u_ger_b")
        assert timedelta(hours=1) < r.expires_at - AHORA <= timedelta(hours=24)

    def test_un_qr_caducado_no_entrega_nada(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        svc.challenges.items[c.challenge_id].expires_at = AHORA - timedelta(seconds=1)
        with pytest.raises(ChallengeExpiredError):
            svc.claim(c.challenge_id, SECRETO)

    def test_un_qr_anulado_avisa_a_la_computadora(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        svc.challenges.items[c.challenge_id].status = "denied"
        assert svc.claim(c.challenge_id, SECRETO).status == "denied"


# ──────────────────────────────────────────────────────────────────────
# Abrir sesión escaneando el QR de una caja autorizada
# ──────────────────────────────────────────────────────────────────────


class TestDesbloqueo:
    def _caja(self, servicio, users=None, devices=None):
        dev = equipo_autorizado()
        svc = servicio(users=users or [fila("u_eva", P.EMPLOYEE, code="0004")], devices=devices or [dev])
        c = svc.create_unlock_challenge(dev, HASH, None, "1.2.3.4")
        return svc, c

    def test_un_empleado_abre_su_propia_sesion(self, servicio):
        svc, c = self._caja(servicio)
        aprobar(svc, c, usuario(P.EMPLOYEE, user_id="u_eva"), code=None)
        r = svc.claim(c.challenge_id, SECRETO)
        assert r.status == "approved"
        assert r.login.access_token == "jwt-de-u_eva"
        assert r.login.user_id == "u_eva" and r.login.position == "empleado"

    def test_la_sesion_es_de_quien_aprobo_no_de_quien_reclama(self, servicio):
        svc, c = self._caja(servicio, users=[fila("u_eva", P.EMPLOYEE), fila("u_otra", P.EMPLOYEE, code="0005")])
        aprobar(svc, c, usuario(P.EMPLOYEE, user_id="u_otra"), code=None)
        assert svc.claim(c.challenge_id, SECRETO).login.user_id == "u_otra"

    def test_no_pide_el_codigo_de_cuatro_digitos(self, servicio):
        svc, c = self._caja(servicio)
        assert aprobar(svc, c, usuario(P.EMPLOYEE, user_id="u_eva"), code=None).status == "approved"

    def test_el_qr_de_una_caja_de_otro_negocio_no_existe_para_ti(self, servicio):
        svc, c = self._caja(servicio)
        with pytest.raises(ChallengeNotFoundError):
            aprobar(svc, c, usuario(P.EMPLOYEE, tenant_id="t_bbb"), code=None)
        with pytest.raises(ChallengeNotFoundError):
            svc.info(usuario(P.EMPLOYEE, tenant_id="t_bbb"), c.challenge_id)

    def test_si_la_caja_ya_no_esta_autorizada_no_se_puede_aprobar(self, servicio):
        svc, c = self._caja(servicio)
        svc.devices.devices[0].revoked_at = AHORA
        with pytest.raises(ChallengeNotFoundError):
            aprobar(svc, c, usuario(P.EMPLOYEE, user_id="u_eva"), code=None)

    def test_una_persona_dada_de_baja_no_recibe_sesion(self, servicio):
        svc, c = self._caja(servicio, users=[fila("u_eva", P.EMPLOYEE, active=False)])
        aprobar(svc, c, usuario(P.EMPLOYEE, user_id="u_eva"), code=None)
        with pytest.raises(ChallengeNotFoundError):
            svc.claim(c.challenge_id, SECRETO)

    def test_la_info_dice_que_caja_es_y_no_pide_codigo(self, servicio):
        svc, c = self._caja(servicio)
        info = svc.info(usuario(P.EMPLOYEE), c.challenge_id)
        assert (info.kind, info.device_name, info.requires_code) == ("unlock", "Caja 1", False)

    def test_la_info_de_un_enroll_pide_codigo_y_describe_el_equipo(self, servicio):
        svc = servicio()
        c = pedir_enroll(svc)
        info = svc.info(usuario(P.MANAGER), c.challenge_id)
        assert info.requires_code is True and info.device_label == "Chrome en Windows"


# ──────────────────────────────────────────────────────────────────────
# Entrar con correo o código de empleado
# ──────────────────────────────────────────────────────────────────────


class TestEntrarEnLaCaja:
    def _svc(self, servicio):
        users = [
            fila("u_eva", P.EMPLOYEE, code="0004", email="eva@example.com"),
            fila("u_otro", P.EMPLOYEE, tenant_id="t_bbb", code="0004", email="otro@example.com"),
            fila("u_baja", P.EMPLOYEE, code="0007", email="baja@example.com", active=False),
        ]
        return servicio(users=users, devices=[equipo_autorizado()])

    def _entrar(self, svc, identificador, clave="Segura123"):
        return svc.login_on_device(svc.devices.devices[0], DeviceLoginRequest(identifier=identificador, password=clave), "1.2.3.4")

    def test_con_el_correo(self, servicio):
        assert self._entrar(self._svc(servicio), "eva@example.com").user_id == "u_eva"

    def test_con_el_correo_sin_distinguir_mayusculas(self, servicio):
        assert self._entrar(self._svc(servicio), "EVA@Example.com").user_id == "u_eva"

    def test_con_el_codigo_de_empleado(self, servicio):
        assert self._entrar(self._svc(servicio), "0004").user_id == "u_eva"

    def test_el_codigo_se_completa_con_ceros(self, servicio):
        assert self._entrar(self._svc(servicio), "4").user_id == "u_eva"

    def test_el_codigo_es_del_negocio_de_la_caja_no_de_otro(self, servicio):
        """El 0004 del otro negocio existe, pero esta caja solo ve a su propio negocio."""
        assert self._entrar(self._svc(servicio), "0004").tenant_id == "t_aaa"

    def test_el_correo_de_otro_negocio_no_entra(self, servicio):
        with pytest.raises(InvalidCredentialsError):
            self._entrar(self._svc(servicio), "otro@example.com")

    @pytest.mark.parametrize("identificador,clave", [
        ("eva@example.com", "equivocada1"), ("0004", "equivocada1"),
        ("9999", "Segura123"), ("nadie@example.com", "Segura123"), ("eva", "Segura123"),
        ("baja@example.com", "Segura123"), ("0007", "Segura123"),
    ])
    def test_credenciales_malas_o_persona_de_baja(self, servicio, identificador, clave):
        with pytest.raises(InvalidCredentialsError):
            self._entrar(self._svc(servicio), identificador, clave)

    def test_el_error_es_el_mismo_para_un_codigo_que_no_existe_y_una_clave_mala(self, servicio):
        svc = self._svc(servicio)
        errores = []
        for ident, clave in (("9999", "x"), ("0004", "x")):
            with pytest.raises(InvalidCredentialsError) as e:
                self._entrar(svc, ident, clave)
            errores.append(type(e.value))
        assert errores[0] is errores[1]


# ──────────────────────────────────────────────────────────────────────
# Cobrar no depende del equipo
# ──────────────────────────────────────────────────────────────────────


class TestCobrarNoExigeEquipoAutorizado:
    """
    Quien entra con su correo desde su celular o computadora cobra sin que nadie autorice
    su equipo. El equipo autorizado solo sirve para la pantalla de la caja registradora.
    """

    @pytest.fixture
    def cobrar(self, monkeypatch):
        from db.database import get_db
        from orders import routes as orders_routes
        from tests.test_cobro_idempotente import _respuesta_de_venta

        class ServicioFalso:
            def __init__(self, db):
                pass

            def create_pos_sale_idempotent(self, *a, **k):
                return _respuesta_de_venta(), False

        monkeypatch.setattr(orders_routes, "OrderService", ServicioFalso)
        app.dependency_overrides[get_db] = lambda: SimpleNamespace()

        def enviar(puesto, headers=None):
            app.dependency_overrides[get_current_user] = lambda: usuario(puesto)
            return TestClient(app, raise_server_exceptions=False).post(
                "/api/v1/orders/business/sales",
                json={"items": [{"product_id": "p_1", "quantity": 1}]},
                headers=headers or {},
            )

        try:
            yield enviar
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)

    @pytest.mark.parametrize("puesto", list(P.POSITIONS))
    def test_cualquier_puesto_cobra_sin_equipo_autorizado(self, cobrar, puesto):
        r = cobrar(puesto)
        assert r.status_code == 201, r.text

    @pytest.mark.parametrize("puesto", [P.AREA_LEAD, P.EMPLOYEE])
    def test_un_token_de_equipo_invalido_tampoco_estorba(self, cobrar, puesto):
        """El cobro ya no mira la cabecera del equipo: un token viejo o inventado no lo bloquea."""
        r = cobrar(puesto, headers={"X-Device-Token": "inventado"})
        assert r.status_code == 201, r.text

    def test_las_rutas_del_equipo_siguen_exigiendo_el_token(self):
        """Lo que sí sigue protegido: preguntar por el estado del equipo sin token da 403."""
        r = TestClient(app, raise_server_exceptions=False).get("/api/v1/device-auth/current")
        assert r.status_code == 403
        assert r.json()["code"] == "DEVICE_NOT_AUTHORIZED"


# ──────────────────────────────────────────────────────────────────────
# Lista, baja y detalles
# ──────────────────────────────────────────────────────────────────────


class TestListaYBaja:
    def test_lista_solo_los_equipos_vigentes_del_negocio(self, servicio):
        vencido = equipo_autorizado("dv_v", ttl=timedelta(seconds=-5), token="a")
        revocado = equipo_autorizado("dv_r", token="b")
        revocado.revoked_at = AHORA
        svc = servicio(devices=[equipo_autorizado("dv_ok", token="c"), vencido, revocado, equipo_autorizado("dv_b", tenant_id="t_bbb", token="d")])
        assert [d.device_id for d in svc.list_devices(usuario(P.MANAGER)).items] == ["dv_ok"]

    def test_la_baja_desautoriza_y_queda_en_la_bitacora(self, servicio):
        svc = servicio(devices=[equipo_autorizado()])
        svc.revoke(usuario(P.MANAGER), "dv_1")
        assert svc.devices.devices[0].revoked_at is not None
        assert svc.audit[-1]["action"] == "REVOKE_DEVICE"

    def test_no_se_da_de_baja_el_equipo_de_otro_negocio(self, servicio):
        svc = servicio(devices=[equipo_autorizado(tenant_id="t_bbb")])
        with pytest.raises(ChallengeNotFoundError):
            svc.revoke(usuario(P.MANAGER, tenant_id="t_aaa"), "dv_1")
        assert svc.devices.devices[0].revoked_at is None

    def test_un_equipo_dado_de_baja_deja_de_valer(self, servicio):
        svc = servicio(devices=[equipo_autorizado(token="tok")])
        assert svc.devices.get_valid_by_token_hash(sha256("tok"), AHORA) is not None
        svc.revoke(usuario(P.MANAGER), "dv_1")
        assert svc.devices.get_valid_by_token_hash(sha256("tok"), AHORA) is None

    def test_un_equipo_vencido_deja_de_valer(self, servicio):
        svc = servicio(devices=[equipo_autorizado(token="tok", ttl=timedelta(seconds=-1))])
        assert svc.devices.get_valid_by_token_hash(sha256("tok"), AHORA) is None


class TestDescribirElNavegador:
    @pytest.mark.parametrize("ua,esperado", [
        ("Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36", "Chrome en Windows"),
        ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120 Safari/537.36 Edg/120", "Edge en Windows"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605 Version/17 Safari/604", "Safari en iOS"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0", "Firefox en Linux"),
        ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537 Chrome/120 Mobile Safari/537", "Chrome en Android"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605 Version/17 Safari/605", "Safari en macOS"),
        ("", "Equipo desconocido"),
        (None, "Equipo desconocido"),
    ])
    def test_describe(self, ua, esperado):
        assert describe_user_agent(ua) == esperado


# ──────────────────────────────────────────────────────────────────────
# Qué rutas son públicas, a propósito
# ──────────────────────────────────────────────────────────────────────


def _llamadas(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _llamadas(dep)


class TestRutasPublicas:
    def test_solo_estas_dos_rutas_de_equipos_no_piden_sesion_ni_equipo(self):
        """
        Pedir un QR y recogerlo las usa una computadora que aún no tiene sesión. Si
        alguien agrega otra ruta pública aquí sin querer, esta prueba falla.
        """
        from devices.dependencies import require_device
        from devices.routes import router

        publicas = set()
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            llamadas = list(_llamadas(route.dependant))
            protegida = require_device in llamadas or any(hasattr(c, "permissions") for c in llamadas)
            if not protegida:
                for m in route.methods:
                    publicas.add((m, route.path))
        assert publicas == {("POST", "/enroll-challenges"), ("POST", "/challenges/{challenge_id}/claim")}
