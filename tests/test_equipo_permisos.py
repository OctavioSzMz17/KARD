"""
Pruebas del equipo del negocio: puestos, permisos y jerarquía.

Lo que protegen:

  * Que cada puesto tenga EXACTAMENTE los permisos acordados: ni uno más (un
    empleado que edita precios) ni uno menos (una cajera que no puede cobrar).
  * Que la jerarquía no se pueda saltar: nadie se sube el puesto, nadie toca a
    su par o a su superior, y el dueño nunca queda a merced de otro.
  * Que NINGUNA ruta del negocio quede sin guardia de permiso: esconder un
    botón en la pantalla no protege nada si la API deja pasar a cualquiera.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from core import permissions as P
from core.dependencies import CurrentUser, get_current_user, get_tenant_id
from core.exceptions import EmailAlreadyExistsError, TeamHierarchyError, UserNotFoundError
from main import app
from team.models import CreateMemberRequest, ResetPasswordRequest, UpdateMemberRequest
from team.service import TeamService
from tests.conftest import FakeSession


# ──────────────────────────────────────────────────────────────────────
# Fábricas
# ──────────────────────────────────────────────────────────────────────


def usuario(position: str, tenant_id: str = "t_aaa", user_id: str | None = None) -> CurrentUser:
    """Un usuario autenticado con el puesto dado (el dueño es `business_admin`, el resto `staff`)."""
    owner = position == P.OWNER
    return CurrentUser(
        user_id=user_id or f"u_{position}",
        tenant_id=tenant_id,
        business_type="restaurant",
        role="business_admin" if owner else "staff",
        email=f"{position}@example.com",
        job_title=P.title_from_position(position),
    )


class FakeUserRepository:
    """Repositorio de usuarios en memoria que respeta el negocio y el índice del código."""

    def __init__(self, users=None):
        self.users = list(users or [])
        self.fail_code_once = False

    def get_by_email(self, email):
        return next((u for u in self.users if u.email == email), None)

    def get_by_id(self, user_id, tenant_id):
        return next((u for u in self.users if u.id == user_id and u.tenant_id == tenant_id), None)

    def list_for_tenant(self, tenant_id):
        return [u for u in self.users if u.tenant_id == tenant_id and u.role != "saas_admin"]

    def next_employee_code(self, tenant_id):
        codes = [int(u.employee_code) for u in self.users if u.tenant_id == tenant_id and u.employee_code]
        return str(max(codes, default=0) + 1).zfill(4)

    def create(self, user):
        if self.fail_code_once:
            self.fail_code_once = False
            raise IntegrityError("insert", {}, Exception('duplicate key value violates unique constraint "uq_users_tenant_employee_code"'))
        user.last_login = None
        self.users.append(user)
        return user

    def save(self, user):
        return user


def fila(user_id, position, tenant_id="t_aaa", code="0001", email=None, active=True):
    """Un usuario ya guardado, tal como lo devolvería la base."""
    return SimpleNamespace(
        id=user_id, tenant_id=tenant_id, email=email or f"{user_id}@example.com",
        role="business_admin" if position == P.OWNER else "staff",
        job_title=P.title_from_position(position), full_name=f"Nombre {user_id}",
        employee_code=code, is_active=active, last_login=None, password_hash="x",
    )


@pytest.fixture
def equipo(monkeypatch):
    """Un TeamService con repositorio falso, sin bitácora real ni bcrypt (en esta máquina passlib no lo soporta)."""
    from team import service as service_module

    llamadas = []
    monkeypatch.setattr(service_module, "log_operation", lambda **kw: llamadas.append(kw))
    monkeypatch.setattr(service_module, "hash_password", lambda pw: f"hash:{pw}")

    def _build(users=None):
        svc = TeamService.__new__(TeamService)
        svc.db = FakeSession()
        svc.users = FakeUserRepository(users)
        svc.audit = llamadas
        return svc

    return _build


def alta(nombre="Ana López", email="ana@example.com", position="empleado", password="Segura123"):
    return CreateMemberRequest(full_name=nombre, email=email, password=password, position=position)


# ──────────────────────────────────────────────────────────────────────
# Los paquetes de permisos
# ──────────────────────────────────────────────────────────────────────


class TestPaquetesDePermisos:
    def test_el_empleado_solo_cobra_consulta_y_abre_su_propia_sesion(self):
        assert P.permissions_of(P.EMPLOYEE) == {P.POS_SELL, P.PRODUCTS_READ, P.CASH_VIEW, P.DEVICES_UNLOCK}

    def test_solo_la_gerencia_autoriza_equipos(self):
        con_permiso = [p for p in P.POSITIONS if P.DEVICES_AUTHORIZE in P.permissions_of(p)]
        assert con_permiso == [P.OWNER, P.MANAGER, P.ASSISTANT_MANAGER]

    def test_todo_el_personal_puede_abrir_sesion_en_una_caja(self):
        for puesto in P.POSITIONS:
            assert P.DEVICES_UNLOCK in P.permissions_of(puesto), puesto

    def test_el_empleado_no_puede_nada_de_gestion(self):
        for prohibido in (P.PRODUCTS_WRITE, P.INVENTORY_ADJUST, P.CASH_MANAGE, P.SALES_READ,
                          P.SALES_MANAGE, P.TEAM_MANAGE, P.SETTINGS_MANAGE, P.BILLING_MANAGE):
            assert prohibido not in P.permissions_of(P.EMPLOYEE), prohibido

    def test_el_encargado_de_area_es_empleado_mas_ajustar_inventario(self):
        assert P.permissions_of(P.AREA_LEAD) == P.permissions_of(P.EMPLOYEE) | {P.INVENTORY_ADJUST}

    @pytest.mark.parametrize("puesto", [P.MANAGER, P.ASSISTANT_MANAGER])
    def test_gerente_y_subgerente_supervisan_pero_no_tocan_suscripcion_ni_personalizacion(self, puesto):
        permisos = P.permissions_of(puesto)
        assert {P.PRODUCTS_WRITE, P.CASH_MANAGE, P.SALES_READ, P.SALES_MANAGE, P.TEAM_MANAGE, P.POS_SELL} <= permisos
        assert P.BILLING_MANAGE not in permisos
        assert P.SETTINGS_MANAGE not in permisos

    def test_gerente_y_subgerente_tienen_el_mismo_paquete(self):
        """Los separa el nivel (quién puede dar de alta a quién), no los permisos."""
        assert P.permissions_of(P.MANAGER) == P.permissions_of(P.ASSISTANT_MANAGER)

    def test_el_dueno_puede_todo(self):
        assert P.permissions_of(P.OWNER) == P.ALL_PERMISSIONS

    def test_ningun_puesto_tiene_un_permiso_que_no_existe(self):
        for puesto in P.POSITIONS:
            assert P.permissions_of(puesto) <= P.ALL_PERMISSIONS

    def test_cada_puesto_tiene_menos_o_igual_que_el_de_arriba(self):
        """La jerarquía de autoridad nunca da más permisos a quien está más abajo."""
        for superior, inferior in zip(P.POSITIONS, P.POSITIONS[1:]):
            assert P.permissions_of(inferior) <= P.permissions_of(superior), (superior, inferior)


class TestPuestoEfectivo:
    def test_business_admin_es_el_dueno(self):
        assert P.position_of("business_admin", None) == P.OWNER

    def test_staff_con_puesto(self):
        assert P.position_of("staff", "gerente") == P.MANAGER
        assert P.position_of("staff", "subgerente") == P.ASSISTANT_MANAGER
        assert P.position_of("staff", "encargado_area") == P.AREA_LEAD

    def test_staff_sin_puesto_es_empleado(self):
        assert P.position_of("staff", None) == P.EMPLOYEE

    def test_un_puesto_desconocido_cae_al_mas_bajo_nunca_al_mas_alto(self):
        assert P.position_of("staff", "director_general") == P.EMPLOYEE

    def test_el_dueno_no_se_obtiene_por_el_puesto(self):
        """Poner job_title='dueno' en un staff no lo vuelve dueño."""
        assert P.position_of("staff", "dueno") == P.EMPLOYEE

    def test_la_plataforma_se_trata_como_dueno(self):
        assert P.position_of("saas_admin", None) == P.OWNER


# ──────────────────────────────────────────────────────────────────────
# La jerarquía
# ──────────────────────────────────────────────────────────────────────


class TestJerarquia:
    def test_quien_puede_dar_de_alta_a_quien(self):
        assert P.assignable_positions(P.OWNER) == [P.MANAGER, P.ASSISTANT_MANAGER, P.AREA_LEAD, P.EMPLOYEE]
        assert P.assignable_positions(P.MANAGER) == [P.ASSISTANT_MANAGER, P.AREA_LEAD, P.EMPLOYEE]
        assert P.assignable_positions(P.ASSISTANT_MANAGER) == [P.AREA_LEAD, P.EMPLOYEE]
        assert P.assignable_positions(P.AREA_LEAD) == [P.EMPLOYEE]
        assert P.assignable_positions(P.EMPLOYEE) == []

    def test_nadie_puede_dar_el_puesto_de_dueno(self):
        for puesto in P.POSITIONS:
            assert P.OWNER not in P.assignable_positions(puesto)

    def test_nadie_puede_dar_su_propio_puesto(self):
        for puesto in P.POSITIONS:
            assert puesto not in P.assignable_positions(puesto)

    @pytest.mark.parametrize("actor", P.POSITIONS)
    def test_nadie_administra_a_su_par_ni_a_su_superior(self, actor):
        for objetivo in P.POSITIONS:
            esperado = P.level_of(actor) < P.level_of(objetivo)
            assert P.can_manage(actor, objetivo) is esperado


# ──────────────────────────────────────────────────────────────────────
# Alta de personas
# ──────────────────────────────────────────────────────────────────────


class TestAlta:
    def test_el_dueno_registra_a_cualquier_puesto_y_el_codigo_lo_pone_el_sistema(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER, code="0001")])
        dueno = usuario(P.OWNER, user_id="u_dueno")

        codigos = []
        for i, puesto in enumerate(["gerente", "subgerente", "encargado_area", "empleado"]):
            m = svc.create_member(dueno, alta(email=f"p{i}@example.com", position=puesto))
            assert m.position == puesto
            codigos.append(m.employee_code)

        assert codigos == ["0002", "0003", "0004", "0005"]

    def test_la_persona_queda_como_personal_nunca_como_dueno(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER)])
        svc.create_member(usuario(P.OWNER, user_id="u_dueno"), alta(position="gerente"))
        creado = svc.users.users[-1]
        assert creado.role == "staff"
        assert creado.job_title == "gerente"

    def test_el_empleado_queda_sin_puesto(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER)])
        svc.create_member(usuario(P.OWNER, user_id="u_dueno"), alta(position="empleado"))
        assert svc.users.users[-1].job_title is None

    def test_la_contrasena_se_guarda_cifrada(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER)])
        svc.create_member(usuario(P.OWNER, user_id="u_dueno"), alta(password="Segura123"))
        guardado = svc.users.users[-1].password_hash
        assert guardado != "Segura123" and guardado.startswith("hash:")

    @pytest.mark.parametrize("actor,pedido", [
        (P.MANAGER, "gerente"),                  # no puede nombrar a su par
        (P.ASSISTANT_MANAGER, "gerente"),        # ni a su superior
        (P.ASSISTANT_MANAGER, "subgerente"),     # ni a su par
        (P.AREA_LEAD, "encargado_area"),
        (P.EMPLOYEE, "empleado"),                # un empleado no registra a nadie
    ])
    def test_nadie_registra_a_alguien_de_su_nivel_o_superior(self, equipo, actor, pedido):
        svc = equipo([fila("u_dueno", P.OWNER)])
        with pytest.raises(TeamHierarchyError):
            svc.create_member(usuario(actor), alta(position=pedido))
        assert len(svc.users.users) == 1          # y no se creó nada

    @pytest.mark.parametrize("actor,pedido", [
        (P.MANAGER, "subgerente"), (P.MANAGER, "empleado"),
        (P.ASSISTANT_MANAGER, "encargado_area"), (P.ASSISTANT_MANAGER, "empleado"),
    ])
    def test_los_supervisores_registran_a_quienes_estan_por_debajo(self, equipo, actor, pedido):
        svc = equipo([fila("u_dueno", P.OWNER)])
        assert svc.create_member(usuario(actor), alta(position=pedido)).position == pedido

    def test_no_se_puede_registrar_a_otro_dueno(self):
        with pytest.raises(ValidationError):
            CreateMemberRequest(full_name="Otro Dueño", email="o@example.com", password="Segura123", position="dueno")

    def test_correo_repetido_se_rechaza(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER, email="ana@example.com")])
        with pytest.raises(EmailAlreadyExistsError):
            svc.create_member(usuario(P.OWNER, user_id="u_dueno"), alta(email="ANA@example.com"))

    def test_el_codigo_es_por_negocio_no_global(self, equipo):
        svc = equipo([fila("u_a", P.OWNER, tenant_id="t_aaa", code="0001"),
                      fila("u_b", P.OWNER, tenant_id="t_bbb", code="0001"),
                      fila("u_b2", P.EMPLOYEE, tenant_id="t_bbb", code="0002")])
        m = svc.create_member(usuario(P.OWNER, user_id="u_a"), alta())
        assert m.employee_code == "0002"          # el del negocio A, aunque B ya va en 0002

    def test_si_dos_altas_chocan_en_el_codigo_se_reintenta_con_el_siguiente(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER)])
        svc.users.fail_code_once = True
        m = svc.create_member(usuario(P.OWNER, user_id="u_dueno"), alta())
        assert m.employee_code == "0002"
        assert svc.db.rollbacks == 1

    def test_las_contrasenas_debiles_se_rechazan(self):
        for mala in ("sololetras", "12345678"):
            with pytest.raises(ValidationError):
                alta(password=mala)

    def test_queda_en_la_bitacora_sin_la_contrasena(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER)])
        svc.create_member(usuario(P.OWNER, user_id="u_dueno"), alta(password="Segura123"))
        assert svc.audit[-1]["action"] == "CREATE_TEAM_MEMBER"
        assert "Segura123" not in str(svc.audit[-1])


# ──────────────────────────────────────────────────────────────────────
# Cambios
# ──────────────────────────────────────────────────────────────────────


class TestCambios:
    def _negocio(self, equipo):
        return equipo([
            fila("u_dueno", P.OWNER, code="0001"),
            fila("u_ger", P.MANAGER, code="0002"),
            fila("u_ger2", P.MANAGER, code="0003"),
            fila("u_sub", P.ASSISTANT_MANAGER, code="0004"),
            fila("u_emp", P.EMPLOYEE, code="0005"),
        ])

    def test_el_dueno_cambia_el_puesto_de_un_gerente(self, equipo):
        svc = self._negocio(equipo)
        r = svc.update_member(usuario(P.OWNER, user_id="u_dueno"), "u_ger", UpdateMemberRequest(position="subgerente"))
        assert r.position == "subgerente"

    def test_nadie_se_sube_el_puesto_a_si_mismo(self, equipo):
        svc = self._negocio(equipo)
        with pytest.raises(TeamHierarchyError):
            svc.update_member(usuario(P.ASSISTANT_MANAGER, user_id="u_sub"), "u_sub", UpdateMemberRequest(position="gerente"))

    def test_nadie_se_da_de_baja_a_si_mismo(self, equipo):
        svc = self._negocio(equipo)
        with pytest.raises(TeamHierarchyError):
            svc.update_member(usuario(P.MANAGER, user_id="u_ger"), "u_ger", UpdateMemberRequest(is_active=False))

    def test_un_gerente_no_toca_a_otro_gerente(self, equipo):
        svc = self._negocio(equipo)
        with pytest.raises(TeamHierarchyError):
            svc.update_member(usuario(P.MANAGER, user_id="u_ger"), "u_ger2", UpdateMemberRequest(is_active=False))

    def test_un_subgerente_no_toca_a_un_gerente(self, equipo):
        svc = self._negocio(equipo)
        with pytest.raises(TeamHierarchyError):
            svc.update_member(usuario(P.ASSISTANT_MANAGER, user_id="u_sub"), "u_ger", UpdateMemberRequest(is_active=False))

    def test_nadie_toca_al_dueno(self, equipo):
        svc = self._negocio(equipo)
        for actor in (P.MANAGER, P.ASSISTANT_MANAGER):
            with pytest.raises(TeamHierarchyError):
                svc.update_member(usuario(actor), "u_dueno", UpdateMemberRequest(is_active=False))

    def test_ni_siquiera_otro_dueno_toca_al_dueno(self, equipo):
        """Un dueño no puede dar de baja a otro dueño (ni al último): son pares."""
        svc = equipo([fila("u_d1", P.OWNER, code="0001"), fila("u_d2", P.OWNER, code="0002")])
        with pytest.raises(TeamHierarchyError):
            svc.update_member(usuario(P.OWNER, user_id="u_d1"), "u_d2", UpdateMemberRequest(is_active=False))

    def test_un_supervisor_no_asciende_a_alguien_por_encima_de_si(self, equipo):
        svc = self._negocio(equipo)
        with pytest.raises(TeamHierarchyError):
            svc.update_member(usuario(P.ASSISTANT_MANAGER, user_id="u_sub"), "u_emp", UpdateMemberRequest(position="gerente"))
        with pytest.raises(TeamHierarchyError):
            svc.update_member(usuario(P.ASSISTANT_MANAGER, user_id="u_sub"), "u_emp", UpdateMemberRequest(position="subgerente"))

    def test_un_supervisor_si_asciende_hasta_un_nivel_por_debajo_del_suyo(self, equipo):
        svc = self._negocio(equipo)
        r = svc.update_member(usuario(P.ASSISTANT_MANAGER, user_id="u_sub"), "u_emp", UpdateMemberRequest(position="encargado_area"))
        assert r.position == "encargado_area"

    def test_un_gerente_da_de_baja_a_un_empleado_y_lo_reactiva(self, equipo):
        svc = self._negocio(equipo)
        gerente = usuario(P.MANAGER, user_id="u_ger")
        assert svc.update_member(gerente, "u_emp", UpdateMemberRequest(is_active=False)).is_active is False
        assert svc.update_member(gerente, "u_emp", UpdateMemberRequest(is_active=True)).is_active is True

    def test_no_se_toca_a_alguien_de_otro_negocio(self, equipo):
        """Ni siquiera se confirma que exista: es un 404, no un 403."""
        svc = equipo([fila("u_otro", P.EMPLOYEE, tenant_id="t_bbb")])
        with pytest.raises(UserNotFoundError):
            svc.update_member(usuario(P.OWNER, tenant_id="t_aaa"), "u_otro", UpdateMemberRequest(is_active=False))

    def test_el_cambio_queda_en_la_bitacora_con_antes_y_despues(self, equipo):
        svc = self._negocio(equipo)
        svc.update_member(usuario(P.OWNER, user_id="u_dueno"), "u_emp", UpdateMemberRequest(position="encargado_area"))
        registro = svc.audit[-1]
        assert registro["action"] == "UPDATE_TEAM_MEMBER"
        assert registro["before"]["position"] == "empleado" and registro["after"]["position"] == "encargado_area"


class TestRestablecerContrasena:
    def test_un_gerente_restablece_la_de_un_empleado(self, equipo):
        svc = equipo([fila("u_ger", P.MANAGER), fila("u_emp", P.EMPLOYEE)])
        svc.reset_password(usuario(P.MANAGER, user_id="u_ger"), "u_emp", ResetPasswordRequest(password="Nueva12345"))
        assert svc.users.get_by_id("u_emp", "t_aaa").password_hash == "hash:Nueva12345"

    def test_no_se_restablece_la_de_un_superior_ni_la_de_un_par(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER), fila("u_ger", P.MANAGER), fila("u_ger2", P.MANAGER)])
        for objetivo in ("u_dueno", "u_ger2"):
            with pytest.raises(TeamHierarchyError):
                svc.reset_password(usuario(P.MANAGER, user_id="u_ger"), objetivo, ResetPasswordRequest(password="Nueva12345"))

    def test_la_contrasena_nunca_llega_a_la_bitacora(self, equipo):
        svc = equipo([fila("u_ger", P.MANAGER), fila("u_emp", P.EMPLOYEE)])
        svc.reset_password(usuario(P.MANAGER, user_id="u_ger"), "u_emp", ResetPasswordRequest(password="Nueva12345"))
        assert svc.audit[-1]["action"] == "RESET_TEAM_PASSWORD"
        assert "Nueva12345" not in str(svc.audit[-1])


class TestListado:
    def test_ordena_por_autoridad_y_marca_a_quien_se_puede_administrar(self, equipo):
        svc = equipo([
            fila("u_emp", P.EMPLOYEE, code="0005"),
            fila("u_dueno", P.OWNER, code="0001"),
            fila("u_ger", P.MANAGER, code="0002"),
            fila("u_ger2", P.MANAGER, code="0003"),
            fila("u_otro", P.EMPLOYEE, tenant_id="t_bbb", code="0001"),
        ])
        r = svc.list_team(usuario(P.MANAGER, user_id="u_ger"))

        assert [m.user_id for m in r.items] == ["u_dueno", "u_ger", "u_ger2", "u_emp"]   # sin el de otro negocio
        assert {m.user_id: m.can_manage for m in r.items} == {
            "u_dueno": False, "u_ger": False, "u_ger2": False, "u_emp": True,
        }

    def test_ofrece_solo_los_puestos_que_quien_consulta_puede_dar(self, equipo):
        svc = equipo([fila("u_sub", P.ASSISTANT_MANAGER)])
        r = svc.list_team(usuario(P.ASSISTANT_MANAGER, user_id="u_sub"))
        assert [o.value for o in r.assignable_positions] == ["encargado_area", "empleado"]

    def test_la_plataforma_no_aparece_en_el_equipo(self, equipo):
        svc = equipo([fila("u_dueno", P.OWNER), SimpleNamespace(**{**fila("u_saas", P.OWNER).__dict__, "role": "saas_admin"})])
        r = svc.list_team(usuario(P.OWNER, user_id="u_dueno"))
        assert [m.user_id for m in r.items] == ["u_dueno"]


# ──────────────────────────────────────────────────────────────────────
# Las rutas: ninguna del negocio sin guardia
# ──────────────────────────────────────────────────────────────────────

#: Rutas que cualquier cuenta autenticada puede usar, porque solo devuelven lo suyo.
SOLO_AUTENTICADAS = {("GET", "/api/v1/auth/me")}


def _llamadas(dependant):
    """Todas las dependencias de una ruta, también las anidadas."""
    for dep in dependant.dependencies:
        yield dep.call
        yield from _llamadas(dep)


def _routers():
    """
    Cada router con el prefijo con que `main.py` lo registra.

    Se recorren los routers directamente y no `app.routes`: FastAPI agrupa lo
    incluido en objetos internos que cambian de una versión a otra. La prueba
    `test_la_lista_de_routers_coincide_con_la_app` avisa si `main.py` registra
    uno que aquí no esté.
    """
    from auth.routes import router as auth
    from billing.routes import router as billing
    from cart.routes import router as cart
    from cash.routes import router as cash
    from consumers.routes import router as consumers
    from devices.routes import router as devices
    from explore.routes import router as explore
    from images.routes import router as images
    from orders.routes import router as orders
    from products.routes import router as products
    from recovery.routes import router as recovery
    from recipes.routes import router as recipes
    from saasadmin.routes import router as saasadmin
    from settings.routes import router as settings
    from storefront.routes import router as storefront
    from team.routes import router as team
    from tickets.routes import router as tickets

    return [
        ("/api/v1/auth", auth), ("/api/v1/saasadmin", saasadmin), ("/api/v1/products", products),
        ("/api/v1/products", images), ("/api/v1/products", recipes), ("/s", storefront),
        ("/api/v1/consumers", consumers), ("/api/v1/billing", billing), ("/api/v1/explore", explore),
        ("/api/v1/cart", cart), ("/api/v1/orders", orders), ("/api/v1/cash", cash),
        ("/api/v1/settings", settings), ("/api/v1/team", team), ("/api/v1/device-auth", devices),
        ("/api/v1/tickets", tickets), ("/api/v1/auth", recovery),
    ]


def _todas_las_rutas():
    for prefijo, router in _routers():
        for route in router.routes:
            if isinstance(route, APIRoute):
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    yield method, prefijo + route.path, route


def _rutas_con_usuario():
    for method, path, route in _todas_las_rutas():
        llamadas = list(_llamadas(route.dependant))
        if get_current_user in llamadas or get_tenant_id in llamadas:
            yield method, path, llamadas


def _guardias(llamadas):
    """Permisos exigidos por la ruta, o los roles si la protege un guardia de rol (solo la plataforma)."""
    return [c.permissions for c in llamadas if hasattr(c, "permissions")] + \
           [c.roles for c in llamadas if hasattr(c, "roles")]


class TestCoberturaDeRutas:
    def test_la_lista_de_routers_coincide_con_la_app(self):
        """Si se registra un router nuevo en main.py, esta prueba obliga a incluirlo en la revisión."""
        en_la_app = {
            (m.upper(), path)
            for path, item in app.openapi()["paths"].items()
            for m in item
            if m.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        }
        # `/health` se define directo en main.py; el webhook de Stripe no sale en el
        # esquema público (se autentica con la firma de Stripe, no con un usuario).
        en_la_app.discard(("GET", "/health"))
        revisadas = {(m, path) for m, path, r in _todas_las_rutas() if r.include_in_schema}
        assert en_la_app == revisadas

    def test_hay_rutas_del_negocio_que_revisar(self):
        assert len(list(_rutas_con_usuario())) >= 30

    def test_toda_ruta_que_identifica_a_un_usuario_exige_un_permiso(self):
        """
        Si alguien agrega una ruta nueva con `Depends(get_current_user)` y olvida
        el permiso, esta prueba falla: así una ruta nueva no nace abierta para
        cualquier empleado.
        """
        sin_guardia = [
            f"{m} {path}" for m, path, llamadas in _rutas_con_usuario()
            if not _guardias(llamadas) and (m, path) not in SOLO_AUTENTICADAS
        ]
        assert sin_guardia == []


#: El permiso que debe pedir cada ruta (método, ruta).
ESPERADO = {
    ("POST", "/api/v1/products"): P.PRODUCTS_WRITE,
    ("GET", "/api/v1/products"): P.PRODUCTS_READ,
    ("GET", "/api/v1/products/lookup"): P.PRODUCTS_READ,
    ("GET", "/api/v1/products/low-stock"): P.PRODUCTS_READ,
    ("GET", "/api/v1/products/{product_id}"): P.PRODUCTS_READ,
    ("PATCH", "/api/v1/products/{product_id}"): P.PRODUCTS_WRITE,
    ("DELETE", "/api/v1/products/{product_id}"): P.PRODUCTS_WRITE,
    ("POST", "/api/v1/products/{product_id}/sell"): P.INVENTORY_ADJUST,
    ("POST", "/api/v1/products/{product_id}/restock"): P.INVENTORY_ADJUST,
    ("POST", "/api/v1/products/{product_id}/images"): P.PRODUCTS_WRITE,
    ("GET", "/api/v1/products/{product_id}/images"): P.PRODUCTS_READ,
    ("POST", "/api/v1/products/{product_id}/recipe/items"): P.PRODUCTS_WRITE,
    ("GET", "/api/v1/products/{product_id}/recipe"): P.PRODUCTS_READ,
    ("POST", "/api/v1/orders/business/sales"): P.POS_SELL,
    ("GET", "/api/v1/orders/business/sales"): P.SALES_READ,
    ("GET", "/api/v1/orders/business/stats"): P.SALES_READ,
    ("PATCH", "/api/v1/orders/business/sales/{order_id}/status"): P.SALES_MANAGE,
    ("GET", "/api/v1/cash/current"): P.CASH_VIEW,
    ("POST", "/api/v1/cash/open"): P.CASH_MANAGE,
    ("POST", "/api/v1/cash/close"): P.CASH_MANAGE,
    ("GET", "/api/v1/cash/sessions"): P.CASH_MANAGE,
    ("GET", "/api/v1/billing/plan"): P.BILLING_MANAGE,
    ("POST", "/api/v1/billing/checkout"): P.BILLING_MANAGE,
    ("GET", "/api/v1/settings/branding"): P.SETTINGS_MANAGE,
    ("PUT", "/api/v1/settings/branding"): P.SETTINGS_MANAGE,
    ("PUT", "/api/v1/settings/fulfillment"): P.SETTINGS_MANAGE,
    ("GET", "/api/v1/team"): P.TEAM_MANAGE,
    ("POST", "/api/v1/team"): P.TEAM_MANAGE,
    ("PATCH", "/api/v1/team/{user_id}"): P.TEAM_MANAGE,
    ("POST", "/api/v1/team/{user_id}/password"): P.TEAM_MANAGE,
    ("GET", "/api/v1/device-auth/devices"): P.DEVICES_AUTHORIZE,
    ("DELETE", "/api/v1/device-auth/devices/{device_id}"): P.DEVICES_AUTHORIZE,
    ("POST", "/api/v1/device-auth/release"): P.DEVICES_UNLOCK,
    ("GET", "/api/v1/tickets/current"): P.POS_SELL,
    ("POST", "/api/v1/tickets/ops"): P.POS_SELL,
}


class TestPermisoPorRuta:
    @pytest.mark.parametrize("metodo,ruta", sorted(ESPERADO))
    def test_cada_ruta_pide_el_permiso_acordado(self, metodo, ruta):
        encontradas = {(m, p): _guardias(ll) for m, p, ll in _rutas_con_usuario()}
        assert (metodo, ruta) in encontradas, f"la ruta {metodo} {ruta} no existe"
        assert encontradas[(metodo, ruta)] == [(ESPERADO[(metodo, ruta)],)]


# ──────────────────────────────────────────────────────────────────────
# El servidor responde 403 de verdad (no solo está declarado)
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def cliente():
    """Cliente de pruebas; cada prueba fija QUIÉN es con `como(puesto)`."""
    client = TestClient(app, raise_server_exceptions=False)

    def como(position: str):
        app.dependency_overrides[get_current_user] = lambda: usuario(position)
        return client

    yield como
    app.dependency_overrides.pop(get_current_user, None)


PROHIBIDO_AL_EMPLEADO = [
    ("post", "/api/v1/products", {"product_type": "final", "name": "X", "price": "1", "metadata": {}}),
    ("patch", "/api/v1/products/p_1", {"name": "Y"}),
    ("delete", "/api/v1/products/p_1", None),
    ("post", "/api/v1/products/p_1/restock", {"quantity": 1}),
    ("post", "/api/v1/products/p_1/images", {"url": "https://x.test/a.jpg"}),
    ("get", "/api/v1/orders/business/sales", None),
    ("patch", "/api/v1/orders/business/sales/o_1/status", {"status": "canceled"}),
    ("post", "/api/v1/cash/open", {"opening_float": "100"}),
    ("post", "/api/v1/cash/close", {"counted_cash": "100"}),
    ("get", "/api/v1/cash/sessions", None),
    ("get", "/api/v1/settings/branding", None),
    ("put", "/api/v1/settings/fulfillment", {}),
    ("get", "/api/v1/billing/plan", None),
    ("post", "/api/v1/billing/checkout", {}),
    ("get", "/api/v1/team", None),
    ("post", "/api/v1/team", {"full_name": "Ana", "email": "a@example.com", "password": "Segura123"}),
    ("patch", "/api/v1/team/u_1", {"is_active": False}),
    ("get", "/api/v1/device-auth/devices", None),
    ("delete", "/api/v1/device-auth/devices/dv_1", None),
]


class TestRespuesta403:
    @pytest.mark.parametrize("metodo,ruta,cuerpo", PROHIBIDO_AL_EMPLEADO)
    def test_el_empleado_recibe_403(self, cliente, metodo, ruta, cuerpo):
        r = getattr(cliente(P.EMPLOYEE), metodo)(ruta, **({"json": cuerpo} if cuerpo is not None else {}))
        assert r.status_code == 403, f"{metodo.upper()} {ruta} → {r.status_code}"

    @pytest.mark.parametrize("metodo,ruta,cuerpo", [
        x for x in PROHIBIDO_AL_EMPLEADO
        if x[1] not in ("/api/v1/products/p_1/restock",)        # el encargado SÍ ajusta inventario
    ])
    def test_el_encargado_de_area_tampoco_pasa_de_ahi(self, cliente, metodo, ruta, cuerpo):
        r = getattr(cliente(P.AREA_LEAD), metodo)(ruta, **({"json": cuerpo} if cuerpo is not None else {}))
        assert r.status_code == 403, f"{metodo.upper()} {ruta} → {r.status_code}"

    @pytest.mark.parametrize("metodo,ruta,cuerpo", [
        ("get", "/api/v1/settings/branding", None),
        ("put", "/api/v1/settings/fulfillment", {}),
        ("get", "/api/v1/billing/plan", None),
        ("post", "/api/v1/billing/checkout", {}),
    ])
    def test_los_supervisores_tampoco_tocan_suscripcion_ni_personalizacion(self, cliente, metodo, ruta, cuerpo):
        for puesto in (P.MANAGER, P.ASSISTANT_MANAGER):
            r = getattr(cliente(puesto), metodo)(ruta, **({"json": cuerpo} if cuerpo is not None else {}))
            assert r.status_code == 403, f"{puesto} {metodo.upper()} {ruta} → {r.status_code}"

    def test_sin_sesion_es_401(self):
        r = TestClient(app).get("/api/v1/products")
        assert r.status_code == 401

    def test_el_403_no_revela_detalles(self, cliente):
        r = cliente(P.EMPLOYEE).get("/api/v1/team")
        assert r.status_code == 403
        assert "permiso" in r.json()["detail"].lower()
