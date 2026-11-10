"""
Recuperar la contraseña por correo, de punta a punta contra la base REAL (Neon), sin dejar rastro.

Lo que las pruebas unitarias no pueden ver porque ahí el repositorio es falso: las consultas SQL de
verdad (anular enlaces anteriores, gastar el enlace una sola vez, caducidad), el HTTP completo, que
las sesiones abiertas antes del cambio dejen de valer y que el bloqueo por intentos fallidos se quite.

Corre dentro de una transacción exterior que se REVIERTE al final. Necesita la migración 013.
El correo no se manda: se captura el enlace que se habría enviado.

    python -P tests/integracion/e2e_recuperar.py
"""
import os
import sys

# Al FINAL de la ruta: si hubiera una carpeta suelta llamada `logging`, ganaría la librería estándar.
sys.path.append(os.getcwd())

import bcrypt
import auth.service as _auth_service
import consumers.service as _consumers_service
import recovery.service as _recovery_service
import team.service as _team_service

_verificar = lambda plain, hashed: bcrypt.checkpw(plain.encode()[:72], hashed.encode())
_hashear = lambda plain: bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt(4)).decode()
_auth_service.verify_password = _verificar
_consumers_service.verify_password = _verificar
_consumers_service.hash_password = _hashear
_recovery_service.hash_password = _hashear
_team_service.hash_password = _hashear

import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.database import engine, get_db
from main import app
from recovery import routes as _routes

conn = engine.connect()
outer = conn.begin()


def sesion():
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()


app.dependency_overrides[get_db] = sesion
c = TestClient(app)
fallos = []


def check(nombre, ok, detalle=""):
    print(("  OK   " if ok else "  FALLA") + f" {nombre}" + (f"  → {detalle}" if (detalle and not ok) else ""))
    if not ok:
        fallos.append(nombre)


# ── Correo capturado y Redis falso (para los topes y el bloqueo) ───────
class FakeRedis:
    def __init__(self):
        self.d = {}

    def incr(self, k):
        self.d[k] = int(self.d.get(k, 0)) + 1
        return self.d[k]

    def expire(self, k, s):
        pass

    def set(self, k, v, ex=None):
        self.d[k] = v

    def exists(self, k):
        return k in self.d

    def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None)


redis = FakeRedis()
_auth_service.get_redis = lambda: redis
_recovery_service.get_redis = lambda: redis

enviados = []   # ("reset"|"changed", destinatario, enlace)
_routes.NotificationService.password_reset = staticmethod(lambda **kw: enviados.append(("reset", kw["email"], kw["link"])))
_routes.NotificationService.password_changed = staticmethod(lambda **kw: enviados.append(("changed", kw["email"], None)))


def token_de(link):
    return link.split("#token=")[1]


def ultimo_enlace(correo):
    for tipo, a, link in reversed(enviados):
        if tipo == "reset" and a == correo:
            return link
    return None


FORGOT, RESET = "/api/v1/auth/forgot-password", "/api/v1/auth/reset-password"


def pedir(correo, lado="negocio", ip="203.0.113.7"):
    return c.post(FORGOT, json={"email": correo, "audience": lado}, headers={"X-Forwarded-For": ip})


def restablecer(token, clave="NuevaClave99"):
    return c.post(RESET, json={"token": token, "password": clave})


def entrar(correo, clave):
    return c.post("/api/v1/auth/login", json={"email": correo, "password": clave})


def n(sql, **p):
    return conn.execute(text(sql), p).scalar()


def cuenta():
    return (n("select count(*) from users"), n("select count(*) from consumers"), n("select count(*) from password_resets"))


antes = cuenta()
print(f"Estado inicial (usuarios, clientes, enlaces): {antes}\n")

# ── Preparación: una empleada creada por el dueño ──────────────────────
dueno = c.post("/api/v1/auth/login", json={"email": "admin@sabor-norteno.com", "password": "testpass123!"})
assert dueno.status_code == 200, f"login del dueño: {dueno.status_code} {dueno.text[:100]}"
h_dueno = {"Authorization": "Bearer " + dueno.json()["access_token"]}
r = c.post("/api/v1/team", headers=h_dueno, json={"full_name": "Eva Recupera", "email": "Eva.Recupera@example.com", "password": "Vieja12345", "position": "empleado"})
assert r.status_code == 201, r.text[:150]
EVA = "eva.recupera@example.com"
sesion_vieja = entrar(EVA, "Vieja12345").json()["access_token"]
h_vieja = {"Authorization": "Bearer " + sesion_vieja}
assert c.get("/api/v1/auth/me", headers=h_vieja).status_code == 200
time.sleep(1.2)   # que el cambio de contraseña quede en un segundo POSTERIOR al de la sesión vieja

print("1) Pedir el enlace")
a = pedir(EVA)
b = pedir("nadie.existe@example.com")
check("un correo registrado → 202", a.status_code == 202, a.text[:120])
check("un correo que no existe → 202, y la respuesta es IDÉNTICA", b.status_code == 202 and a.json() == b.json(), f"{a.json()} / {b.json()}")
check("solo se mandó correo a la cuenta que existe", [x[1] for x in enviados if x[0] == "reset"] == [EVA], str(enviados))
check("no se creó enlace para el correo inexistente", n("select count(*) from password_resets") == antes[2] + 1)
link1 = ultimo_enlace(EVA)
tok1 = token_de(link1)
check("el enlace lleva el token en el fragmento (#), no en la consulta (?)", "/restablecer#token=" in link1 and "?" not in link1, link1)
fila = conn.execute(text("select token_hash, expires_at - created_at, used_at, requested_ip, account_kind from password_resets order by created_at desc limit 1")).one()
import hashlib
check("en la base solo está la HUELLA del enlace, no el enlace", fila[0] == hashlib.sha256(tok1.encode()).hexdigest() and tok1 not in fila[0])
check("dura 1 hora", fila[1] == timedelta(hours=1), str(fila[1]))
check("queda anotado desde qué dirección se pidió", fila[3] == "203.0.113.7", str(fila[3]))
check("y de qué lado es la cuenta", fila[4] == "user")
check("el token no aparece en ninguna parte de la tabla", n("select count(*) from password_resets where cast(password_resets as text) like :t", t=f"%{tok1}%") == 0)

print("\n2) Correo en mayúsculas/espacios, y pedir otro anula el anterior")
pedir("  EVA.RECUPERA@Example.com ")
link2 = ultimo_enlace(EVA)
tok2 = token_de(link2)
check("el correo se normaliza y llega otro enlace distinto", tok2 != tok1)
check("el enlace anterior quedó anulado en la base", n("select used_at is not null from password_resets where token_hash=:h", h=hashlib.sha256(tok1.encode()).hexdigest()) is True)
viejo = restablecer(tok1)
check("usar el enlace viejo → 400 INVALID_RESET_TOKEN", viejo.status_code == 400 and viejo.json().get("code") == "INVALID_RESET_TOKEN", viejo.text[:150])
check("y la contraseña sigue siendo la anterior", entrar(EVA, "Vieja12345").status_code == 200)

print("\n3) Contraseña débil no gasta el enlace")
for mala in ("corta1", "sinnumeroslargo", "123456789"):
    r = restablecer(tok2, mala)
    check(f"'{mala}' → 422", r.status_code == 422, r.text[:100])
check("el enlace sigue sin usar", n("select used_at is null from password_resets where token_hash=:h", h=hashlib.sha256(tok2.encode()).hexdigest()) is True)

print("\n4) Restablecer de verdad (con bloqueo por intentos fallidos de por medio)")
for _ in range(5):
    entrar(EVA, "mal-intento-1")
bloqueada = entrar(EVA, "Vieja12345")
check("5 intentos fallidos bloquean la cuenta (ni la clave buena entra)", bloqueada.status_code != 200, str(bloqueada.status_code))
ok = restablecer(tok2, "NuevaClave99")
check("con el enlace vigente → 200", ok.status_code == 200, ok.text[:150])
check("se avisó por correo que la contraseña cambió", ("changed", EVA, None) in enviados)
check("la contraseña NUEVA entra", entrar(EVA, "NuevaClave99").status_code == 200)
check("la contraseña VIEJA ya no", entrar(EVA, "Vieja12345").status_code == 401)
check("el bloqueo por intentos fallidos se quitó", f"auth:lockout:{EVA}" not in redis.d)
check("en la base queda anotada la fecha del cambio", n("select password_changed_at is not null from users where email=:e", e=EVA) is True)

print("\n5) Las sesiones anteriores dejan de valer")
check("la sesión abierta ANTES del cambio → 401", c.get("/api/v1/auth/me", headers=h_vieja).status_code == 401)
nueva = entrar(EVA, "NuevaClave99").json()["access_token"]
check("una sesión abierta DESPUÉS sí vale", c.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + nueva}).status_code == 200)
check("el dueño no se vio afectado", c.get("/api/v1/auth/me", headers=h_dueno).status_code == 200)

print("\n6) Un enlace sirve una sola vez")
otra = restablecer(tok2, "OtraClave88")
check("reusar el enlace gastado → 400", otra.status_code == 400 and otra.json().get("code") == "INVALID_RESET_TOKEN", otra.text[:120])
check("y la contraseña no cambió", entrar(EVA, "NuevaClave99").status_code == 200)
inventado = restablecer("A" * 43)
check("un enlace inventado → el MISMO error", inventado.status_code == 400 and inventado.json() == otra.json(), inventado.text[:120])

print("\n7) Caducidad")
pedir(EVA)
tok3 = token_de(ultimo_enlace(EVA))
conn.execute(text("update password_resets set expires_at = :t where token_hash=:h"), {"t": datetime.now(timezone.utc) - timedelta(seconds=1), "h": hashlib.sha256(tok3.encode()).hexdigest()})
cad = restablecer(tok3)
check("un enlace caducado → 400", cad.status_code == 400 and cad.json().get("code") == "INVALID_RESET_TOKEN", cad.text[:120])

print("\n8) Cuenta dada de baja")
pedir(EVA)
tok4 = token_de(ultimo_enlace(EVA))
conn.execute(text("update users set is_active=false where email=:e"), {"e": EVA})
baja = restablecer(tok4)
check("si la cuenta se dio de baja, su enlace ya no sirve → 400", baja.status_code == 400, baja.text[:120])
antes_baja = n("select count(*) from password_resets")
redis.d.clear()
pedir(EVA)
check("y una cuenta de baja no recibe enlaces nuevos", n("select count(*) from password_resets") == antes_baja)
conn.execute(text("update users set is_active=true where email=:e"), {"e": EVA})

print("\n9) El enlace se gasta de forma atómica")
from recovery.repository import RecoveryRepository
s = Session(bind=conn, join_transaction_mode="create_savepoint")
repo = RecoveryRepository(s)
redis.d.clear()
pedir(EVA)
tok5 = token_de(ultimo_enlace(EVA))
check("(prueba) el enlace pedido es nuevo", tok5 != tok4)
rid = n("select id from password_resets where token_hash=:h", h=hashlib.sha256(tok5.encode()).hexdigest())
ahora = datetime.now(timezone.utc)
primera, segunda = repo.consume(rid, ahora), repo.consume(rid, ahora)
check("la primera petición lo gasta y la segunda no", (primera, segunda) == (True, False), f"{primera}, {segunda}")
redis.d.clear()
pedir(EVA)
tok6 = token_de(ultimo_enlace(EVA))
rid6 = n("select id from password_resets where token_hash=:h", h=hashlib.sha256(tok6.encode()).hexdigest())
conn.execute(text("update password_resets set expires_at = :t where id=:i"), {"t": ahora - timedelta(seconds=1), "i": rid6})
check("aun si el servicio no lo revisara, el UPDATE atómico no gasta un enlace caducado", repo.consume(rid6, ahora) is False)
s.close()

print("\n9b) Los enlaces de una cuenta no tocan los de otra")
redis.d.clear()
pedir(EVA)
tok_eva = token_de(ultimo_enlace(EVA))
pedir("admin@sabor-norteno.com")
pedir("admin@optica-claridad.com")
check("(prueba) llegaron tres enlaces distintos", len({x[2] for x in enviados if x[0] == "reset" and x[2]}) >= 3)
check("el enlace de Eva sigue sin usar después de que otras cuentas pidieron el suyo",
      n("select used_at is null from password_resets where token_hash=:h", h=hashlib.sha256(tok_eva.encode()).hexdigest()) is True)
check("y todavía le sirve", restablecer(tok_eva, "ClaveOtra77").status_code == 200)
check("restablecer la de Eva tampoco anuló los enlaces de las otras cuentas",
      n("select count(*) from password_resets pr join users u on u.id = pr.account_id "
        "where u.email in ('admin@sabor-norteno.com','admin@optica-claridad.com') and pr.used_at is null") == 2)

print("\n10) Topes de solicitudes")
redis.d.clear()
base = n("select count(*) from password_resets")
for _ in range(5):
    pedir(EVA, ip="198.51.100.9")
check("por correo: solo 3 por hora generan enlace", n("select count(*) from password_resets") - base == 3, str(n("select count(*) from password_resets") - base))
check("el 4.º y 5.º igual contestaron 202 (no delatan el tope)", pedir(EVA, ip="198.51.100.9").status_code == 202)

print("\n11) Cliente de la tienda (otro lado de la plataforma)")
redis.d.clear()
rc = c.post("/api/v1/consumers/register", json={"email": "ana.recupera@example.com", "password": "ClaveVieja1", "name": "Ana"})
assert rc.status_code in (200, 201), rc.text[:150]
ANA = "ana.recupera@example.com"
tok_c_viejo = c.post("/api/v1/consumers/login", json={"email": ANA, "password": "ClaveVieja1"}).json()["access_token"]
h_c_viejo = {"Authorization": "Bearer " + tok_c_viejo}
assert c.get("/api/v1/consumers/me", headers=h_c_viejo).status_code == 200
time.sleep(1.2)
antes_c = n("select count(*) from password_resets")
pedir(ANA, "negocio")
check("un cliente NO recibe enlace pidiéndolo por el lado del negocio", n("select count(*) from password_resets") == antes_c)
pedir(EVA, "cliente")
check("y un empleado tampoco por el lado del cliente", n("select count(*) from password_resets") == antes_c)
pedir(ANA, "cliente")
check("por su lado sí", n("select count(*) from password_resets") == antes_c + 1)
check("el enlace es de tipo cliente", n("select account_kind from password_resets order by created_at desc limit 1") == "consumer")
tokc = token_de(ultimo_enlace(ANA))
check("restablece → 200", restablecer(tokc, "ClaveNueva2").status_code == 200)
check("la nueva entra", c.post("/api/v1/consumers/login", json={"email": ANA, "password": "ClaveNueva2"}).status_code == 200)
check("la vieja no", c.post("/api/v1/consumers/login", json={"email": ANA, "password": "ClaveVieja1"}).status_code == 401)
check("su sesión anterior → 401", c.get("/api/v1/consumers/me", headers=h_c_viejo).status_code == 401)
nueva_c = c.post("/api/v1/consumers/login", json={"email": ANA, "password": "ClaveNueva2"}).json()["access_token"]
check("la sesión nueva vale", c.get("/api/v1/consumers/me", headers={"Authorization": "Bearer " + nueva_c}).status_code == 200)

print("\n12) Nada se arrastra")
outer.rollback()
conn.close()
with engine.connect() as limpio:
    despues = (
        limpio.execute(text("select count(*) from users")).scalar(),
        limpio.execute(text("select count(*) from consumers")).scalar(),
        limpio.execute(text("select count(*) from password_resets")).scalar(),
    )
check("la base quedó exactamente como estaba", despues == antes, f"{antes} → {despues}")

print()
if fallos:
    print(f"FALLARON {len(fallos)}:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("Todo bien: la recuperación de contraseña funciona de punta a punta.")
