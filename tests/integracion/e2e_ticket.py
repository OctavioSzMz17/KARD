"""
Recorrido de punta a punta del TICKET COMPARTIDO contra la base REAL (Neon), sin dejar rastro.

Reproduce lo que ocurre con tres aparatos: la laptop (la caja), el celular de quien cobra y
la gerencia autorizando. Lo que se prueba aquí y no en las pruebas unitarias es la consulta
SQL de verdad (el candado de la fila del ticket, el operador de la caja, el aislamiento entre
negocios) y el HTTP completo.

Sin Redis en esta máquina, el ticket vive entero en la base: es la ruta de respaldo. La ruta
con Redis se prueba con un Redis simulado en `tests/test_ticket_compartido.py`.

Corre dentro de una transacción exterior que se REVIERTE al final: los `commit()` de los
servicios son puntos de guardado, así que nada sobrevive. Al terminar se comprueba que los
conteos de la base quedaron iguales.

No lo recoge pytest (no se llama `test_*`): necesita la base real y los negocios de
demostración (`seeders/seed_demo_tenants.py`) y la migración 011. Se corre a mano:

    python -P tests/integracion/e2e_ticket.py
"""
import os
import sys

# Al FINAL de la ruta: si hubiera una carpeta suelta llamada `logging`, ganaría la librería estándar.
sys.path.append(os.getcwd())

# El bcrypt del equipo es más nuevo de lo que passlib soporta (el contenedor fija un par
# compatible). Solo para esta prueba se cifra y verifica con bcrypt directo.
import bcrypt
import auth.service as _auth_service
import devices.service as _devices_service
import team.service as _team_service

_auth_service.verify_password = lambda plain, hashed: bcrypt.checkpw(plain.encode()[:72], hashed.encode())
_devices_service.verify_password = _auth_service.verify_password
_team_service.hash_password = lambda plain: bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt(4)).decode()

import hashlib
import secrets

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.redis_client import get_redis
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
    r = conn.execute(text(
        "select (select count(*) from users), (select count(*) from orders), "
        "(select count(*) from cash_sessions), (select count(*) from devices), "
        "(select count(*) from auth_challenges), (select count(*) from ticket_drafts)")).one()
    return tuple(int(x) for x in r)


def entrar(correo, clave="testpass123!"):
    r = c.post("/api/v1/auth/login", json={"email": correo, "password": clave})
    assert r.status_code == 200, f"login de {correo}: {r.status_code} {r.text[:120]}"
    return {"Authorization": "Bearer " + r.json()["access_token"]}, r.json()


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
DEV = "/api/v1/device-auth"
TK = "/api/v1/tickets"

_ops = [0]


def op(tipo, producto=None, cantidad=None, uid=None, op_id=None):
    _ops[0] += 1
    d = {"op_id": op_id or f"e2e-op-{secrets.token_hex(8)}-{_ops[0]}", "type": tipo}
    if producto:
        d["product_id"] = producto
    if cantidad:
        d["quantity"] = cantidad
    if uid:
        d["uid"] = uid
    return d


def sha(valor):
    return hashlib.sha256(valor.encode()).hexdigest()


def autorizar_equipo(aprobador, nombre="Caja 1"):
    """La laptop pide su QR, la gerencia lo aprueba con el número de pantalla y la laptop recoge su token."""
    secreto = secrets.token_hex(32)
    ch = c.post(f"{DEV}/enroll-challenges", headers={"User-Agent": UA}, json={"secret_hash": sha(secreto)}).json()
    c.post(f"{DEV}/challenges/{ch['challenge_id']}/approve", headers=aprobador,
           json={"device_name": nombre, "code": ch["verification_code"]})
    return c.post(f"{DEV}/challenges/{ch['challenge_id']}/claim", json={"secret": secreto}).json()["device_token"]


def entrar_a_la_caja_por_qr(token_equipo, celular):
    """La laptop muestra su QR de desbloqueo y la persona lo aprueba desde su celular; la laptop recibe su sesión."""
    secreto = secrets.token_hex(32)
    ch = c.post(f"{DEV}/unlock-challenges", headers={"X-Device-Token": token_equipo}, json={"secret_hash": sha(secreto)}).json()
    c.post(f"{DEV}/challenges/{ch['challenge_id']}/approve", headers=celular, json={})
    r = c.post(f"{DEV}/challenges/{ch['challenge_id']}/claim", json={"secret": secreto}).json()
    return {"Authorization": "Bearer " + r["login"]["access_token"]}


antes = cuenta()
print(f"Estado inicial (usuarios, pedidos, turnos, equipos, QR, tickets): {antes}")
print(f"Redis en esta máquina: {'sí' if get_redis() is not None else 'no (el ticket vive en la base)'}\n")

dueno, _ = entrar("admin@sabor-norteno.com")
otro_dueno, _ = entrar("admin@optica-claridad.com")


def alta(nombre, correo, puesto):
    r = c.post("/api/v1/team", headers=dueno, json={"full_name": nombre, "email": correo, "password": "Segura12345", "position": puesto})
    assert r.status_code == 201, r.text[:150]
    return r.json()


alta("Gina Gerente", "gina.gerente@example.com", "gerente")
alta("Eva Empleada", "eva.ticket@example.com", "empleado")
alta("Luis Empleado", "luis.ticket@example.com", "empleado")
gerente, _ = entrar("gina.gerente@example.com", "Segura12345")
eva, _ = entrar("eva.ticket@example.com", "Segura12345")
luis, _ = entrar("luis.ticket@example.com", "Segura12345")

prods = c.get("/api/v1/products?product_type=final&page_size=100", headers=dueno).json()["items"]
p1 = next(x for x in prods if x["inventory_count"] >= 10)
p2 = next(x for x in prods if x["inventory_count"] >= 10 and x["product_id"] != p1["product_id"])
P1, P2 = p1["product_id"], p2["product_id"]
insumos = c.get("/api/v1/products?product_type=insumo&page_size=5", headers=dueno).json()["items"]

if c.get("/api/v1/cash/current", headers=dueno).json() is None:
    c.post("/api/v1/cash/open", headers=gerente, json={"opening_float": "300"})

print("1) Antes de entrar a una caja")
token = autorizar_equipo(gerente)
laptop_dev = {"X-Device-Token": token}
r = c.get(f"{TK}/current", headers=eva)
check("el celular de Eva, sin caja, recibe register nulo (su ticket se queda local)", r.status_code == 200 and r.json() == {"register": None, "ticket": None}, r.text[:120])
r = c.post(f"{TK}/ops", headers=eva, json=op("add", P1))
check("y no puede mandar cambios a un ticket que no es suyo → 409 NO_REGISTER", r.status_code == 409 and r.json().get("code") == "NO_REGISTER", r.text[:120])

print("2) Eva entra a la caja con su celular (por el QR de la laptop)")
laptop_eva = {**entrar_a_la_caja_por_qr(token, eva), **laptop_dev}
def cajas_de(correo):
    return conn.execute(text(
        "select d.name from devices d join users u on u.id = d.operator_id "
        "where u.email = :e and d.revoked_at is null order by d.name"), {"e": correo}).scalars().all()


check("la base anota que Eva opera la caja", cajas_de("eva.ticket@example.com") == ["Caja 1"], str(cajas_de("eva.ticket@example.com")))
tel = c.get(f"{TK}/current", headers=eva).json()
lap = c.get(f"{TK}/current", headers=laptop_eva).json()
check("el celular de Eva ve su caja sin ningún QR extra", tel["register"] and tel["register"]["name"] == "Caja 1", str(tel))
check("la laptop y el celular ven la MISMA caja", tel["register"]["id"] == lap["register"]["id"])
check("el ticket empieza en blanco, con identificador y revisión 0", tel["ticket"]["rev"] == 0 and tel["ticket"]["lines"] == [] and tel["ticket"]["uid"], str(tel["ticket"]))

print("3) El celular escanea y la laptop lo ve (como un escáner de mano)")
r = c.post(f"{TK}/ops", headers=eva, json=op("add", P1))
check("el celular agrega un producto → 200", r.status_code == 200 and r.json()["ticket"]["rev"] == 1, r.text[:120])
uid = r.json()["ticket"]["uid"]
r = c.get(f"{TK}/current", params={"since": 0, "uid": tel["ticket"]["uid"]}, headers=laptop_eva)
check("la laptop, que tenía la revisión 0, recibe el ticket nuevo", r.status_code == 200 and r.json()["ticket"]["lines"] == [{"product_id": P1, "quantity": 1}], r.text[:150])
r = c.get(f"{TK}/current", params={"since": 1, "uid": uid}, headers=laptop_eva)
check("y si ya lo tiene, la consulta responde 204 sin cuerpo", r.status_code == 204 and r.content == b"", str(r.status_code))

print("4) La laptop también cambia y el celular lo ve")
r = c.post(f"{TK}/ops", headers=laptop_eva, json=op("add", P1))
check("la laptop agrega el mismo producto → se suman, no se duplica la línea", r.json()["ticket"]["lines"] == [{"product_id": P1, "quantity": 2}], r.text[:150])
r = c.post(f"{TK}/ops", headers=laptop_eva, json=op("add", P2, 3))
check("agrega otro con cantidad 3", r.json()["ticket"]["rev"] == 3)
repetida = op("add", P1, op_id="e2e-repetida-0000000001")
c.post(f"{TK}/ops", headers=eva, json=repetida)
r = c.post(f"{TK}/ops", headers=eva, json=repetida)
check("la misma operación enviada dos veces (reintento) no suma dos veces", {l["product_id"]: l["quantity"] for l in r.json()["ticket"]["lines"]}[P1] == 3, r.text[:200])
r = c.post(f"{TK}/ops", headers=laptop_eva, json=op("remove", P2))
check("la laptop borra una línea", [l["product_id"] for l in r.json()["ticket"]["lines"]] == [P1])
check("el celular ve el borrado", [l["product_id"] for l in c.get(f"{TK}/current", headers=eva).json()["ticket"]["lines"]] == [P1])
r = c.post(f"{TK}/ops", headers=eva, json=op("remove", P2))
check("borrar algo que ya no está no es un error → 200", r.status_code == 200 and len(r.json()["ticket"]["lines"]) == 1)
r = c.post(f"{TK}/ops", headers=eva, json=op("set_quantity", P1, 5))
check("cambiar la cantidad", r.json()["ticket"]["lines"] == [{"product_id": P1, "quantity": 5}])

# La copia en la base solo avanza: una copia atrasada (llegó tarde) no pisa una más nueva. Esto es SQL real.
from tickets.repository import PgDraftStore
fila = conn.execute(text("select device_id, tenant_id, rev from ticket_drafts order by updated_at desc limit 1")).one()
s_copia = Session(bind=conn, join_transaction_mode="create_savepoint")
PgDraftStore(s_copia).save_snapshot(fila.tenant_id, fila.device_id, {"uid": "tk_atrasado_000000001", "rev": 0, "lines": [], "ops": []})
s_copia.commit()
s_copia.close()
check("una copia atrasada no pisa una más nueva (SQL real)",
      conn.execute(text("select rev from ticket_drafts where device_id = :d"), {"d": fila.device_id}).scalar() == fila.rev)

print("5) Nadie ve ni toca la caja de otro")
r = c.get(f"{TK}/current", headers=luis)
check("otro empleado (Luis), que no opera esta caja, no la ve", r.json() == {"register": None, "ticket": None}, r.text[:120])
r = c.post(f"{TK}/ops", headers=luis, json=op("add", P1))
check("ni puede agregarle cosas → 409", r.status_code == 409 and r.json().get("code") == "NO_REGISTER")
r = c.get(f"{TK}/current", headers={**otro_dueno, **laptop_dev})
check("el dueño de la óptica con el token de equipo de Sabor Norteño tampoco la ve", r.json() == {"register": None, "ticket": None}, r.text[:150])
r = c.post(f"{TK}/ops", headers=eva, json=op("add", "producto-inventado"))
check("agregar un producto que no existe → 404 y no entra", r.status_code == 404, r.text[:100])
if insumos:
    r = c.post(f"{TK}/ops", headers=eva, json=op("add", insumos[0]["product_id"]))
    check("un insumo (no se vende) tampoco entra → 404", r.status_code == 404, r.text[:100])
r = c.post(f"{TK}/ops", headers=eva, json={"op_id": "corta", "type": "clear"})
check("una operación mal formada → 422", r.status_code == 422)
check("sin sesión no hay acceso → 401", c.get(f"{TK}/current").status_code == 401)

print("6) Cobrar el ticket desde el celular, y que no se duplique si la laptop también lo cobra")
estado = c.get(f"{TK}/current", headers=eva).json()["ticket"]
cuerpo = {"items": [{"product_id": l["product_id"], "quantity": l["quantity"]} for l in estado["lines"]], "payment_method": "card"}
llave = {"Idempotency-Key": estado["uid"]}
stock_antes = c.get(f"/api/v1/products/{P1}", headers=dueno).json()["inventory_count"]
r1 = c.post("/api/v1/orders/business/sales", headers={**eva, **llave}, json=cuerpo)
check("el celular cobra el ticket → 201", r1.status_code == 201, r1.text[:150])
r2 = c.post("/api/v1/orders/business/sales", headers={**laptop_eva, **llave}, json=cuerpo)
check("la laptop intenta cobrar el MISMO ticket → 200 con la misma venta (no se duplica)",
      r2.status_code == 200 and r2.json()["order_id"] == r1.json()["order_id"] and r2.headers.get("idempotent-replay") == "true", r2.text[:150])
stock_despues = c.get(f"/api/v1/products/{P1}", headers=dueno).json()["inventory_count"]
check("el inventario bajó una sola vez", stock_antes - stock_despues == 5, f"{stock_antes} → {stock_despues}")
r = c.post(f"{TK}/ops", headers=eva, json=op("clear", uid=estado["uid"]))
check("tras cobrar, se vacía el ticket y se estrena identificador", r.json()["ticket"]["lines"] == [] and r.json()["ticket"]["uid"] != estado["uid"], r.text[:150])
r = c.post(f"{TK}/ops", headers=laptop_eva, json=op("clear", uid=estado["uid"]))
check("el 'vaciar' tardío del ticket ya cobrado no toca el siguiente", r.status_code == 200 and r.json()["ticket"]["uid"] != estado["uid"])
nuevo = c.get(f"{TK}/current", headers=laptop_eva).json()["ticket"]
c.post(f"{TK}/ops", headers=laptop_eva, json=op("add", P2))
check("la laptop empieza otro ticket y el celular lo ve", c.get(f"{TK}/current", headers=eva).json()["ticket"]["lines"] == [{"product_id": P2, "quantity": 1}])

print("7) Entra otra persona a la caja: el ticket se vacía")
r = c.post(f"{DEV}/login", headers=laptop_dev, json={"identifier": "luis.ticket@example.com", "password": "Segura12345"})
check("Luis entra a la caja con sus datos en la laptop → 200", r.status_code == 200, r.text[:100])
laptop_luis = {"Authorization": "Bearer " + r.json()["access_token"], **laptop_dev}
check("el ticket de la caja quedó en blanco", c.get(f"{TK}/current", headers=laptop_luis).json()["ticket"]["lines"] == [])
check("el celular de Luis ahora ve la caja", c.get(f"{TK}/current", headers=luis).json()["register"]["name"] == "Caja 1")
check("el celular de Eva ya no (dejó de operarla)", c.get(f"{TK}/current", headers=eva).json()["register"] is None)
r = c.post(f"{TK}/ops", headers=luis, json=op("add", P1))
check("Luis escanea desde su celular y su laptop lo ve", r.status_code == 200 and c.get(f"{TK}/current", headers=laptop_luis).json()["ticket"]["lines"] == [{"product_id": P1, "quantity": 1}])

print("8) Cerrar sesión en la caja")
r = c.post(f"{DEV}/release", headers=laptop_eva)
check("Eva (que ya no la opera) no puede soltarla → 204 sin efecto", r.status_code == 204 and c.get(f"{TK}/current", headers=luis).json()["register"] is not None)
r = c.post(f"{DEV}/release", headers=laptop_luis)
check("Luis cierra sesión en la caja → 204", r.status_code == 204, r.text[:100])
check("la caja queda sin operador", c.get(f"{TK}/current", headers=luis).json() == {"register": None, "ticket": None})
filas = conn.execute(text("select count(*) from ticket_drafts")).scalar()
check("y su ticket se borró de la base", filas == antes[5], f"{filas} filas")

print("9) Cerrar la caja del día limpia los tickets de todas las cajas")
laptop_eva = {**entrar_a_la_caja_por_qr(token, eva), **laptop_dev}
token2 = autorizar_equipo(gerente, "Caja 2")
entrar_a_la_caja_por_qr(token2, eva)
check("una persona opera una sola caja a la vez: Eva pasa a la Caja 2 y deja la 1",
      cajas_de("eva.ticket@example.com") == ["Caja 2"], str(cajas_de("eva.ticket@example.com")))
check("su celular ve ahora la Caja 2", c.get(f"{TK}/current", headers=eva).json()["register"]["name"] == "Caja 2")
c.post(f"{TK}/ops", headers=eva, json=op("add", P1))
check("hay un ticket en armado", conn.execute(text("select count(*) from ticket_drafts")).scalar() == antes[5] + 1)
check("la gerente cierra la caja", c.post("/api/v1/cash/close", headers=gerente, json={"counted_cash": "1000"}).status_code == 200)
check("los tickets en armado desaparecen", conn.execute(text("select count(*) from ticket_drafts")).scalar() == antes[5])
check("y la caja ya no se opera (equipo desautorizado)", c.get(f"{TK}/current", headers=eva).json()["register"] is None)

print("10) Reversión")
outer.rollback()
conn.close()
conn2 = engine.connect()
despues = tuple(int(x) for x in conn2.execute(text(
    "select (select count(*) from users), (select count(*) from orders), (select count(*) from cash_sessions), "
    "(select count(*) from devices), (select count(*) from auth_challenges), (select count(*) from ticket_drafts)")).one())
conn2.close()
check(f"la base quedó EXACTAMENTE igual ({antes} → {despues})", antes == despues)

print()
print("TODO BIEN" if not fallos else f"{len(fallos)} FALLOS: " + ", ".join(fallos))
sys.exit(1 if fallos else 0)
