"""
Recorrido de punta a punta del equipo y los permisos contra la base REAL (Neon),
sin dejar rastro.

Cubre lo que las pruebas unitarias no pueden, porque ahí el repositorio es
falso: la consulta SQL de verdad (aislamiento entre negocios, código de empleado
único, baja inmediata) y el HTTP completo (guardias, códigos de error).

Corre dentro de una transacción exterior que se REVIERTE al final: los
`commit()` de los servicios son puntos de guardado, así que ningún usuario,
venta ni turno sobrevive. Al terminar se comprueba que los conteos quedaron iguales.

No lo recoge pytest (no se llama `test_*`): necesita la base real y los negocios
de demostración (`seeders/seed_demo_tenants.py`). Se corre a mano:

    PYTHONPATH=. python tests/integracion/e2e_equipo.py
"""
import sys

# El bcrypt del equipo es más nuevo de lo que passlib soporta (el contenedor
# fija un par compatible). Solo para esta prueba se cifra y verifica con bcrypt directo.
import bcrypt
import auth.service as _auth_service
import devices.service as _devices_service
import team.service as _team_service

_auth_service.verify_password = lambda plain, hashed: bcrypt.checkpw(plain.encode()[:72], hashed.encode())
_devices_service.verify_password = _auth_service.verify_password
_team_service.hash_password = lambda plain: bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt(4)).decode()

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.database import engine, get_db
from main import app

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


def cuenta():
    r = conn.execute(text("select (select count(*) from users), (select count(*) from orders), "
                          "(select count(*) from cash_sessions), (select count(*) from devices), "
                          "(select count(*) from auth_challenges)")).one()
    return tuple(int(x) for x in r)


def entrar(correo, clave="testpass123!"):
    r = c.post("/api/v1/auth/login", json={"email": correo, "password": clave})
    assert r.status_code == 200, f"login de {correo}: {r.status_code} {r.text[:120]}"
    return {"Authorization": "Bearer " + r.json()["access_token"]}, r.json()


import hashlib
import secrets

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
DEV = "/api/v1/device-auth"


def sha(valor):
    return hashlib.sha256(valor.encode()).hexdigest()


def pedir_qr():
    secreto = secrets.token_hex(32)
    r = c.post(f"{DEV}/enroll-challenges", headers={"User-Agent": UA}, json={"secret_hash": sha(secreto)})
    return r, secreto


def autorizar_equipo(aprobador, nombre="Caja 1", abrir=None):
    """Una computadora pide su QR, la gerencia lo aprueba con el código y la computadora recoge su token."""
    r, secreto = pedir_qr()
    ch = r.json()
    cuerpo = {"device_name": nombre, "code": ch["verification_code"]}
    if abrir:
        cuerpo["open_float"] = abrir
    c.post(f"{DEV}/challenges/{ch['challenge_id']}/approve", headers=aprobador, json=cuerpo)
    return c.post(f"{DEV}/challenges/{ch['challenge_id']}/claim", json={"secret": secreto}).json()["device_token"]


antes = cuenta()
print(f"Estado inicial (usuarios, pedidos, turnos, equipos, QR): {antes}\n")

dueno, dueno_resp = entrar("admin@sabor-norteno.com")
otro_dueno, _ = entrar("admin@optica-claridad.com")

print("1) El dueño y su identidad")
check("el login del dueño trae puesto y todos los permisos",
      dueno_resp["position"] == "dueno" and "team:manage" in dueno_resp["permissions"] and "billing:manage" in dueno_resp["permissions"])
me = c.get("/api/v1/auth/me", headers=dueno).json()
check("/auth/me devuelve quién soy", me["position"] == "dueno" and me["employee_code"] == "0001", str(me))

print("2) Registrar al equipo")
def alta(h, nombre, correo, puesto="empleado"):
    return c.post("/api/v1/team", headers=h, json={"full_name": nombre, "email": correo, "password": "Segura12345", "position": puesto})

r = alta(dueno, "Gabriela Gerente", "gabriela.gerente@example.com", "gerente")
check("el dueño registra a una gerente → 201", r.status_code == 201, r.text[:150])
gerente_id = r.json()["user_id"]
check("el código lo genera el sistema (0002)", r.json()["employee_code"] == "0002", r.json().get("employee_code"))
r = alta(dueno, "Esteban Subgerente", "esteban.sub@example.com", "subgerente")
sub_id = r.json()["user_id"]
r = alta(dueno, "Eva Empleada", "eva.empleada@example.com", "empleado")
emp_id = r.json()["user_id"]
check("los códigos siguen en orden (0004 para la tercera alta)", r.json()["employee_code"] == "0004", r.json().get("employee_code"))
r = alta(dueno, "Eva Repetida", "EVA.empleada@example.com")
check("correo repetido (otra capitalización) → 400", r.status_code == 400, r.text[:100])
r = c.post("/api/v1/team", headers=dueno, json={"full_name": "Otro Dueño", "email": "dueno2@example.com", "password": "Segura12345", "position": "dueno"})
check("no se puede registrar a otro dueño → 422", r.status_code == 422, str(r.status_code))
r = c.post("/api/v1/team", headers=dueno, json={"full_name": "Débil", "email": "debil@example.com", "password": "abc", "position": "empleado"})
check("contraseña débil → 422", r.status_code == 422)

print("3) Cada quien con su puesto")
gerente, g_resp = entrar("gabriela.gerente@example.com", "Segura12345")
sub, _ = entrar("esteban.sub@example.com", "Segura12345")
emp, e_resp = entrar("eva.empleada@example.com", "Segura12345")
check("el empleado inicia sesión y su sesión dice 'empleado' con solo 4 permisos (cobrar, ver, caja y abrir su sesión)",
      e_resp["position"] == "empleado" and sorted(e_resp["permissions"]) == ["cash:view", "devices:unlock", "pos:sell", "products:read"], str(e_resp["permissions"]))
check("la gerente trae permisos de supervisor pero no de suscripción",
      "team:manage" in g_resp["permissions"] and "billing:manage" not in g_resp["permissions"])

print("4) Jerarquía por HTTP")
r = alta(gerente, "Otro Gerente", "otro.gerente@example.com", "gerente")
check("una gerente no registra a otra gerente → 403 TEAM_HIERARCHY", r.status_code == 403 and r.json().get("code") == "TEAM_HIERARCHY", r.text[:120])
r = alta(sub, "Sub Dos", "sub.dos@example.com", "subgerente")
check("un subgerente no registra a otro subgerente → 403", r.status_code == 403)
r = alta(sub, "Eli Encargada", "eli.encargada@example.com", "encargado_area")
check("un subgerente sí registra a un encargado de área → 201", r.status_code == 201, r.text[:120])
encargada_id = r.json()["user_id"]
r = alta(emp, "Intrusa", "intrusa@example.com")
check("un empleado no registra a nadie → 403", r.status_code == 403)
r = c.patch(f"/api/v1/team/{gerente_id}", headers=gerente, json={"position": "subgerente"})
check("nadie se cambia el puesto a sí mismo → 403", r.status_code == 403 and r.json().get("code") == "TEAM_HIERARCHY", r.text[:120])
r = c.patch(f"/api/v1/team/{gerente_id}", headers=sub, json={"is_active": False})
check("un subgerente no da de baja a una gerente → 403", r.status_code == 403)
me_id = me["user_id"]
r = c.patch(f"/api/v1/team/{me_id}", headers=gerente, json={"is_active": False})
check("nadie toca al dueño → 403", r.status_code == 403)
r = c.patch(f"/api/v1/team/{emp_id}", headers=sub, json={"position": "gerente"})
check("un subgerente no ascende a nadie por encima de sí → 403", r.status_code == 403)

print("5) Aislamiento entre negocios (la consulta SQL real)")
r = c.patch(f"/api/v1/team/{emp_id}", headers=otro_dueno, json={"is_active": False})
check("el dueño de OTRO negocio no encuentra a mi empleado → 404", r.status_code == 404 and r.json().get("code") == "USER_NOT_FOUND", r.text[:120])
r = c.post(f"/api/v1/team/{emp_id}/password", headers=otro_dueno, json={"password": "Hackeada12345"})
check("ni puede restablecerle la contraseña → 404", r.status_code == 404)
equipo_otro = c.get("/api/v1/team", headers=otro_dueno).json()
check("el equipo de la óptica no incluye gente de Sabor Norteño",
      all(m["email"].endswith(("@optica-claridad.com",)) or "example.com" not in m["email"] for m in equipo_otro["items"])
      and emp_id not in [m["user_id"] for m in equipo_otro["items"]])
equipo_mio = c.get("/api/v1/team", headers=dueno).json()
check("mi equipo incluye a los 4 nuevos y al dueño, y ofrece 4 puestos",
      len(equipo_mio["items"]) == 5 and len(equipo_mio["assignable_positions"]) == 4, str(len(equipo_mio["items"])))
check("el listado va de más a menos autoridad", equipo_mio["items"][0]["position"] == "dueno")
check("a la gerente solo se le ofrecen 3 puestos",
      len(c.get("/api/v1/team", headers=gerente).json()["assignable_positions"]) == 3)

print("6) El empleado: cobrar sí, administrar no")
check("ve el catálogo (200)", c.get("/api/v1/products?page_size=5", headers=emp).status_code == 200)
check("consulta la caja (200)", c.get("/api/v1/cash/current", headers=emp).status_code == 200)
prods = c.get("/api/v1/products?product_type=final&page_size=100", headers=emp).json()["items"]
p = next(x for x in prods if x["inventory_count"] >= 5)
for nombre, r in [
    ("editar un producto", c.patch(f"/api/v1/products/{p['product_id']}", headers=emp, json={"name": "Hackeado"})),
    ("borrar un producto", c.delete(f"/api/v1/products/{p['product_id']}", headers=emp)),
    ("surtir inventario", c.post(f"/api/v1/products/{p['product_id']}/restock", headers=emp, json={"quantity": 5})),
    ("abrir la caja", c.post("/api/v1/cash/open", headers=emp, json={"opening_float": "100"})),
    ("ver el reporte de ventas", c.get("/api/v1/orders/business/sales", headers=emp)),
    ("ver la suscripción", c.get("/api/v1/billing/plan", headers=emp)),
    ("cambiar la personalización", c.put("/api/v1/settings/branding", headers=emp, json={})),
    ("ver el equipo", c.get("/api/v1/team", headers=emp)),
]:
    check(f"NO puede {nombre} → 403", r.status_code == 403, str(r.status_code))
check("el producto sigue intacto tras los intentos",
      c.get(f"/api/v1/products/{p['product_id']}", headers=dueno).json()["name"] == p["name"])

print("6b) Equipos autorizados por QR")
r, sec = pedir_qr()
check("una computadora SIN sesión pide su QR → 201", r.status_code == 201 and len(r.json()["verification_code"]) == 4, r.text[:100])
ch = r.json()
cid = ch["challenge_id"]
check("el QR lleva solo un identificador largo y aleatorio", len(cid) >= 20)
check("sin sesión nadie aprueba → 401", c.post(f"{DEV}/challenges/{cid}/approve", json={"code": ch["verification_code"]}).status_code == 401)
r = c.post(f"{DEV}/challenges/{cid}/approve", headers=emp, json={"device_name": "Caja 1", "code": ch["verification_code"]})
check("un empleado no puede autorizar un equipo → 403", r.status_code == 403 and r.json().get("code") == "FORBIDDEN", r.text[:100])
info = c.get(f"{DEV}/challenges/{cid}", headers=gerente).json()
check("la gerente ve qué equipo pide acceso", info["kind"] == "enroll" and info["device_label"] == "Chrome en Windows" and info["requires_code"] is True, str(info))
mal = "0000" if ch["verification_code"] != "0000" else "1111"
r = c.post(f"{DEV}/challenges/{cid}/approve", headers=gerente, json={"device_name": "Caja 1", "code": mal})
check("código equivocado → 400 WRONG_CODE", r.status_code == 400 and r.json().get("code") == "WRONG_CODE", r.text[:100])
r = c.post(f"{DEV}/challenges/{cid}/approve", headers=gerente, json={"device_name": "Caja 1", "code": ch["verification_code"]})
check("con el código correcto, la gerente autoriza → 200", r.status_code == 200, r.text[:100])
check("quien fotografió el QR no puede reclamar (secreto equivocado) → 404",
      c.post(f"{DEV}/challenges/{cid}/claim", json={"secret": "x" * 64}).status_code == 404)
r = c.post(f"{DEV}/challenges/{cid}/claim", json={"secret": sec})
check("la computadora recoge su token", r.status_code == 200 and r.json()["status"] == "approved" and r.json()["device_token"], r.text[:100])
token = r.json()["device_token"]
check("el token solo se entrega una vez (otro reclamo → 404)", c.post(f"{DEV}/challenges/{cid}/claim", json={"secret": sec}).status_code == 404)
emp_dev = {**emp, "X-Device-Token": token}
check("el equipo consulta su estado → 200 con su nombre", c.get(f"{DEV}/current", headers={"X-Device-Token": token}).json().get("name") == "Caja 1")
check("sin token el equipo no está autorizado → 403 DEVICE_NOT_AUTHORIZED",
      c.get(f"{DEV}/current").status_code == 403 and c.get(f"{DEV}/current").json().get("code") == "DEVICE_NOT_AUTHORIZED")
check("un token inventado no vale", c.get(f"{DEV}/current", headers={"X-Device-Token": "inventado"}).status_code == 403)
lista = c.get(f"{DEV}/devices", headers=gerente).json()["items"]
check("la gerente ve el equipo en su lista", [d["name"] for d in lista] == ["Caja 1"], str(lista))
check("un empleado no ve la lista → 403", c.get(f"{DEV}/devices", headers=emp).status_code == 403)
check("la óptica no ve los equipos de Sabor Norteño", c.get(f"{DEV}/devices", headers=otro_dueno).json()["items"] == [])
r = c.post("/api/v1/orders/business/sales", headers=emp, json={"items": [{"product_id": p["product_id"], "quantity": 1}]})
check("un empleado SIN equipo autorizado ya no recibe 403 por eso (cobrar no depende del equipo)",
      r.status_code != 403, r.text[:120])

print("7) Cobrar al 100 % con el flujo de caja")
if c.get("/api/v1/cash/current", headers=dueno).json() is None:
    r = c.post("/api/v1/orders/business/sales", headers=emp_dev, json={"items": [{"product_id": p["product_id"], "quantity": 1}]})
    check("sin caja abierta, cobrar → 409 CASH_CLOSED (la regla de caja sigue)", r.status_code == 409 and r.json().get("code") == "CASH_CLOSED", r.text[:100])
    check("la gerente abre la caja → 201", c.post("/api/v1/cash/open", headers=gerente, json={"opening_float": "500"}).status_code == 201)
else:
    print("  (ya hay una caja abierta en la base: se omiten las pruebas de caja cerrada; se cierra dentro de la transacción)")
    check("el empleado no puede abrir otra ni cerrar la existente → 403", c.post("/api/v1/cash/open", headers=emp, json={"opening_float": "1"}).status_code == 403)
r = c.post("/api/v1/orders/business/sales", headers=emp_dev, json={"items": [{"product_id": p["product_id"], "quantity": 2}],
                                                                  "payment_method": "cash", "amount_received": "1000"})
check("el empleado cobra → 201", r.status_code == 201, r.text[:150])
check("el ticket dice quién cobró (la parte local de su correo)", r.json().get("cashier") == "eva.empleada", str(r.json().get("cashier")))
r = c.post("/api/v1/orders/business/sales", headers=emp, json={"items": [{"product_id": p["product_id"], "quantity": 1}], "payment_method": "card"})
check("el mismo empleado cobra desde su propio celular, sin equipo autorizado → 201", r.status_code == 201, r.text[:150])
check("el empleado no puede cerrar la caja → 403", c.post("/api/v1/cash/close", headers=emp, json={"counted_cash": "0"}).status_code == 403)
check("la gerente sí la cierra", c.post("/api/v1/cash/close", headers=gerente, json={"counted_cash": "900"}).status_code == 200)
check("al cerrar la caja el equipo queda desautorizado", c.get(f"{DEV}/current", headers={"X-Device-Token": token}).status_code == 403)
check("y con la caja cerrada nadie cobra, tenga o no equipo → 409 CASH_CLOSED",
      c.post("/api/v1/orders/business/sales", headers=emp, json={"items": [{"product_id": p["product_id"], "quantity": 1}]}).status_code == 409)

print("8) Encargada de área: ajusta inventario, nada más")
enc_h, _ = entrar("eli.encargada@example.com", "Segura12345")
r = c.post(f"/api/v1/products/{p['product_id']}/restock", headers=enc_h, json={"quantity": 3})
check("la encargada puede surtir inventario → 200", r.status_code == 200, r.text[:120])
check("pero no editar productos → 403", c.patch(f"/api/v1/products/{p['product_id']}", headers=enc_h, json={"name": "X"}).status_code == 403)

print("9) Cambios y bajas surten efecto al instante")
r = c.patch(f"/api/v1/team/{emp_id}", headers=gerente, json={"position": "encargado_area"})
check("la gerente asciende a la empleada a encargada de área", r.status_code == 200 and r.json()["position"] == "encargado_area", r.text[:120])
check("con el MISMO token, la empleada ya puede surtir inventario",
      c.post(f"/api/v1/products/{p['product_id']}/restock", headers=emp, json={"quantity": 1}).status_code == 200)
me_emp = c.get("/api/v1/auth/me", headers=emp).json()
check("y /auth/me ya lo refleja", me_emp["position"] == "encargado_area" and "inventory:adjust" in me_emp["permissions"])
r = c.post(f"/api/v1/team/{emp_id}/password", headers=gerente, json={"password": "Nueva12345"})
check("la gerente le restablece la contraseña → 204", r.status_code == 204)
check("la contraseña vieja ya no sirve", c.post("/api/v1/auth/login", json={"email": "eva.empleada@example.com", "password": "Segura12345"}).status_code in (400, 401))
check("la nueva sí", c.post("/api/v1/auth/login", json={"email": "eva.empleada@example.com", "password": "Nueva12345"}).status_code == 200)
r = c.patch(f"/api/v1/team/{encargada_id}", headers=dueno, json={"is_active": False})
check("el dueño da de baja a la encargada", r.status_code == 200 and r.json()["is_active"] is False)
check("con su token de antes, ya no entra a nada (401)", c.get("/api/v1/products", headers=enc_h).status_code == 401)
check("y ya no puede iniciar sesión", c.post("/api/v1/auth/login", json={"email": "eli.encargada@example.com", "password": "Segura12345"}).status_code in (400, 401))
r = c.patch(f"/api/v1/team/{encargada_id}", headers=dueno, json={"is_active": True})
check("se puede reactivar", r.status_code == 200 and r.json()["is_active"] is True)

print("9b) Entrar en una caja autorizada")
tok2 = autorizar_equipo(gerente, "Caja 2")
dev2 = {"X-Device-Token": tok2}
r = c.post(f"{DEV}/login", headers=dev2, json={"identifier": "0004", "password": "Nueva12345"})
check("la empleada entra con su CÓDIGO de empleado y su contraseña → 200", r.status_code == 200 and r.json()["user_id"] == emp_id, r.text[:120])
r = c.post(f"{DEV}/login", headers=dev2, json={"identifier": "eva.empleada@example.com", "password": "Nueva12345"})
check("también con su correo", r.status_code == 200 and r.json()["full_name"] == "Eva Empleada", r.text[:120])
check("contraseña equivocada → 401", c.post(f"{DEV}/login", headers=dev2, json={"identifier": "0004", "password": "mala"}).status_code == 401)
check("un código que no existe → 401 (igual que una contraseña mala)", c.post(f"{DEV}/login", headers=dev2, json={"identifier": "9999", "password": "Nueva12345"}).status_code == 401)
check("sin equipo autorizado no hay entrada por código → 403", c.post(f"{DEV}/login", json={"identifier": "0004", "password": "Nueva12345"}).status_code == 403)
check("el correo de OTRO negocio no entra en esta caja → 401",
      c.post(f"{DEV}/login", headers=dev2, json={"identifier": "admin@optica-claridad.com", "password": "testpass123!"}).status_code == 401)

eva_celular, _ = entrar("eva.empleada@example.com", "Nueva12345")
r = c.post(f"{DEV}/unlock-challenges", headers=dev2, json={"secret_hash": sha("u" * 64)})
check("la caja pide un QR de desbloqueo → 201", r.status_code == 201, r.text[:100])
uch = r.json()
check("la óptica no ve ese QR (otro negocio) → 404", c.get(f"{DEV}/challenges/{uch['challenge_id']}", headers=otro_dueno).status_code == 404)
check("ni puede aprobarlo → 404", c.post(f"{DEV}/challenges/{uch['challenge_id']}/approve", headers=otro_dueno, json={}).status_code == 404)
info = c.get(f"{DEV}/challenges/{uch['challenge_id']}", headers=eva_celular).json()
check("Eva ve en su celular a qué caja entra, sin pedir código", info["kind"] == "unlock" and info["device_name"] == "Caja 2" and info["requires_code"] is False, str(info))
check("la caja no recibe nada mientras nadie apruebe",
      c.post(f"{DEV}/challenges/{uch['challenge_id']}/claim", json={"secret": "u" * 64}).json()["status"] == "pending")
check("Eva aprueba desde su celular → 200", c.post(f"{DEV}/challenges/{uch['challenge_id']}/approve", headers=eva_celular, json={}).status_code == 200)
r = c.post(f"{DEV}/challenges/{uch['challenge_id']}/claim", json={"secret": "u" * 64})
check("la caja recibe la sesión de Eva", r.status_code == 200 and r.json()["login"]["user_id"] == emp_id, r.text[:100])
sesion_qr = {"Authorization": "Bearer " + r.json()["login"]["access_token"]}
check("esa sesión es de Eva (puesto actual: encargada de área)", c.get("/api/v1/auth/me", headers=sesion_qr).json()["position"] == "encargado_area")
check("el QR ya usado no entrega otra sesión → 404", c.post(f"{DEV}/challenges/{uch['challenge_id']}/claim", json={"secret": "u" * 64}).status_code == 404)

# Los tres dueños tienen el código 0001: cada caja debe encontrar SOLO el de su negocio.
mi_negocio = me["tenant_id"]
r = c.post(f"{DEV}/login", headers=dev2, json={"identifier": "0001", "password": "testpass123!"})
check("el código 0001 en la caja de Sabor Norteño es SU dueño, no el de otro negocio",
      r.status_code == 200 and r.json()["user_id"] == me["user_id"] and r.json()["tenant_id"] == mi_negocio, r.text[:120])
tok_optica = autorizar_equipo(otro_dueno, "Caja Óptica")
negocio_optica = c.get("/api/v1/auth/me", headers=otro_dueno).json()
r = c.post(f"{DEV}/login", headers={"X-Device-Token": tok_optica}, json={"identifier": "0001", "password": "testpass123!"})
check("el mismo código 0001 en la caja de la óptica es el dueño de la óptica",
      r.status_code == 200 and r.json()["user_id"] == negocio_optica["user_id"] and r.json()["tenant_id"] == negocio_optica["tenant_id"], r.text[:120])

r = c.delete(f"{DEV}/devices/{[d['device_id'] for d in c.get(f'{DEV}/devices', headers=gerente).json()['items']][0]}", headers=gerente)
check("la gerente desautoriza una caja → 204", r.status_code == 204, str(r.status_code))
check("la caja desautorizada ya no inicia sesión → 403", c.post(f"{DEV}/login", headers=dev2, json={"identifier": "0004", "password": "Nueva12345"}).status_code == 403)
check("un empleado no desautoriza cajas → 403", c.delete(f"{DEV}/devices/dv_x", headers=emp).status_code == 403)
check("el dueño de otro negocio no puede desautorizar mis cajas → 404",
      c.delete(f"{DEV}/devices/dv_x", headers=otro_dueno).status_code == 404)

print("10) Sin sesión")
check("sin token → 401", c.get("/api/v1/team").status_code == 401)

print("\n11) Reversión")
outer.rollback()
conn.close()
conn2 = engine.connect()
despues = tuple(int(x) for x in conn2.execute(text(
    "select (select count(*) from users), (select count(*) from orders), (select count(*) from cash_sessions), "
    "(select count(*) from devices), (select count(*) from auth_challenges)")).one())
check(f"la base quedó EXACTAMENTE igual ({antes} → {despues})", despues == antes)

print(f"\n{'TODO EN ORDEN' if not fallos else str(len(fallos)) + ' FALLOS: ' + ', '.join(fallos)}")
sys.exit(1 if fallos else 0)
