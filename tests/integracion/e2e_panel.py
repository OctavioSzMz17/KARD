"""
Recorrido de punta a punta de: quién cobró y desde qué caja, el corte por cajero y las estadísticas
reales del Dashboard (con zona horaria) contra la base REAL (Neon), sin dejar rastro.

Lo que las pruebas unitarias no pueden ver porque ahí el repositorio es falso: las consultas SQL de
verdad (el día local de cada venta, los más vendidos, el corte por cajero, el aislamiento entre
negocios) y el HTTP completo.

Corre dentro de una transacción exterior que se REVIERTE al final. Necesita la migración 012.

    python -P tests/integracion/e2e_panel.py
"""
import os
import sys

# Al FINAL de la ruta: si hubiera una carpeta suelta llamada `logging`, ganaría la librería estándar.
sys.path.append(os.getcwd())

import bcrypt
import auth.service as _auth_service
import devices.service as _devices_service
import team.service as _team_service

_auth_service.verify_password = lambda plain, hashed: bcrypt.checkpw(plain.encode()[:72], hashed.encode())
_devices_service.verify_password = _auth_service.verify_password
_team_service.hash_password = lambda plain: bcrypt.hashpw(plain.encode()[:72], bcrypt.gensalt(4)).decode()

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

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
    r = conn.execute(text(
        "select (select count(*) from users), (select count(*) from orders), "
        "(select count(*) from cash_sessions), (select count(*) from devices), "
        "(select count(*) from auth_challenges), (select count(*) from ticket_drafts)")).one()
    return tuple(int(x) for x in r)


def entrar(correo, clave="testpass123!"):
    r = c.post("/api/v1/auth/login", json={"email": correo, "password": clave})
    assert r.status_code == 200, f"login de {correo}: {r.status_code} {r.text[:120]}"
    return {"Authorization": "Bearer " + r.json()["access_token"]}


DEV = "/api/v1/device-auth"
VENTAS = "/api/v1/orders/business/sales"
STATS = "/api/v1/orders/business/stats"


def sha(valor):
    return hashlib.sha256(valor.encode()).hexdigest()


def autorizar_equipo(aprobador, nombre="Caja 1"):
    secreto = secrets.token_hex(32)
    ch = c.post(f"{DEV}/enroll-challenges", headers={"User-Agent": "Mozilla/5.0 Chrome/120"}, json={"secret_hash": sha(secreto)}).json()
    c.post(f"{DEV}/challenges/{ch['challenge_id']}/approve", headers=aprobador, json={"device_name": nombre, "code": ch["verification_code"]})
    return c.post(f"{DEV}/challenges/{ch['challenge_id']}/claim", json={"secret": secreto}).json()["device_token"]


def entrar_a_la_caja_por_qr(token_equipo, celular):
    secreto = secrets.token_hex(32)
    ch = c.post(f"{DEV}/unlock-challenges", headers={"X-Device-Token": token_equipo}, json={"secret_hash": sha(secreto)}).json()
    c.post(f"{DEV}/challenges/{ch['challenge_id']}/approve", headers=celular, json={})
    r = c.post(f"{DEV}/challenges/{ch['challenge_id']}/claim", json={"secret": secreto}).json()
    return {"Authorization": "Bearer " + r["login"]["access_token"]}


def stats(h, tz, days=7):
    r = c.get(STATS, headers=h, params={"days": days, "tz_offset": tz})
    assert r.status_code == 200, r.text[:200]
    return r.json()


def por_fecha(s):
    return {d["date"]: (D(d["revenue"]), d["orders"]) for d in s["days"]}


antes = cuenta()
print(f"Estado inicial (usuarios, pedidos, turnos, equipos, QR, tickets): {antes}\n")

dueno = entrar("admin@sabor-norteno.com")
otro_dueno = entrar("admin@optica-claridad.com")

r = c.post("/api/v1/team", headers=dueno, json={"full_name": "Eva Panel", "email": "eva.panel@example.com", "password": "Segura12345", "position": "empleado"})
assert r.status_code == 201, r.text[:150]
eva_codigo = r.json()["employee_code"]
eva = entrar("eva.panel@example.com", "Segura12345")

prods = c.get("/api/v1/products?product_type=final&page_size=100", headers=dueno).json()["items"]
p1 = next(x for x in prods if x["inventory_count"] >= 10)
p2 = next(x for x in prods if x["inventory_count"] >= 10 and x["product_id"] != p1["product_id"])
P1, P2 = p1["product_id"], p2["product_id"]

if c.get("/api/v1/cash/current", headers=dueno).json() is None:
    c.post("/api/v1/cash/open", headers=dueno, json={"opening_float": "300"})

base_cajeros = {x["name"]: (x["count"], D(x["total"])) for x in c.get("/api/v1/cash/current", headers=dueno).json()["summary"]["by_cashier"]}
ayer = (datetime.now(timezone.utc) - timedelta(days=1)).date()
base0, base360 = stats(dueno, 0), stats(dueno, 360)
base_optica = stats(otro_dueno, 360)
print(f"Referencia: el día que usará la prueba es {ayer} (UTC)\n")

print("1) Cobrar desde la caja, desde el celular y sin caja")
token = autorizar_equipo(dueno)
laptop = {**entrar_a_la_caja_por_qr(token, eva), "X-Device-Token": token}
cuerpo = lambda p, n: {"items": [{"product_id": p, "quantity": n}], "payment_method": "card"}
v1 = c.post(VENTAS, headers=laptop, json=cuerpo(P1, 2))
check("la laptop cobra → 201", v1.status_code == 201, v1.text[:150])
check("el ticket dice 'Eva P.' (nombre e inicial, no el correo)", v1.json().get("cashier") == "Eva P.", str(v1.json().get("cashier")))
check("y la caja: 'Caja 1'", v1.json().get("register_name") == "Caja 1", str(v1.json().get("register_name")))
v2 = c.post(VENTAS, headers=eva, json=cuerpo(P2, 1))
check("el celular de Eva (sin token de equipo) cobra → 201", v2.status_code == 201, v2.text[:150])
check("y la venta lleva la caja que Eva opera", v2.json().get("register_name") == "Caja 1", str(v2.json().get("register_name")))
check("el ticket del celular también dice 'Eva P.'", v2.json().get("cashier") == "Eva P.")
v3 = c.post(VENTAS, headers=dueno, json=cuerpo(P1, 1))
check("el dueño cobra desde otro equipo, sin caja → 201", v3.status_code == 201, v3.text[:150])
check("su venta no lleva caja", v3.json().get("register_name") is None)
check("y su ticket no muestra un correo", "@" not in (v3.json().get("cashier") or ""), str(v3.json().get("cashier")))
r1 = c.post(VENTAS, headers={**laptop, "Idempotency-Key": "panel-llave-0000000001"}, json=cuerpo(P2, 1))
r2 = c.post(VENTAS, headers={**laptop, "Idempotency-Key": "panel-llave-0000000001"}, json=cuerpo(P2, 1))
check("un reintento devuelve la misma venta con la misma caja", r2.status_code == 200 and r2.json()["order_id"] == r1.json()["order_id"] and r2.json()["register_name"] == "Caja 1", r2.text[:150])
v4 = c.post(VENTAS, headers=dueno, json=cuerpo(P2, 1))
check("una venta más del dueño → 201", v4.status_code == 201)
cancelada = c.patch(f"{VENTAS}/{v4.json()['order_id']}/status", headers=dueno, json={"status": "canceled"})
check("y se cancela → 200 (esa venta NO debe contar en ningún lado)", cancelada.status_code == 200, cancelada.text[:120])
ids = [v["order_id"] for v in (v1.json(), v2.json(), v3.json(), r1.json(), v4.json())]
total_mio = sum(D(v["total"]) for v in (v1.json(), v2.json(), v3.json(), r1.json()))

print("2) El reporte de ventas dice quién cobró y desde dónde")
rep = {v["order_id"]: v for v in c.get(f"{VENTAS}?page_size=20", headers=dueno).json()["items"]}
check("la venta de la laptop trae el nombre completo, el código y la caja",
      (rep[v1.json()["order_id"]]["cashier_name"], rep[v1.json()["order_id"]]["cashier_code"], rep[v1.json()["order_id"]]["register_name"]) == ("Eva Panel", eva_codigo, "Caja 1"),
      str({k: rep[v1.json()["order_id"]].get(k) for k in ("cashier_name", "cashier_code", "register_name")}))
check("la del celular también", rep[v2.json()["order_id"]]["cashier_name"] == "Eva Panel" and rep[v2.json()["order_id"]]["register_name"] == "Caja 1")
check("la del dueño trae su nombre pero ninguna caja", rep[v3.json()["order_id"]]["cashier_name"] and rep[v3.json()["order_id"]]["register_name"] is None)

print("3) El corte dice cuánto cobró cada cajero")
cur = c.get("/api/v1/cash/current", headers=dueno).json()
cajeros = {x["name"]: x for x in cur["summary"]["by_cashier"]}
check("Eva aparece con sus 3 ventas (la venta repetida cuenta una sola vez)", "Eva Panel" in cajeros and cajeros["Eva Panel"]["count"] == 3, str(cajeros.get("Eva Panel")))
esperado_eva = D(v1.json()["total"]) + D(v2.json()["total"]) + D(r1.json()["total"])
check("y su total es lo que cobró", D(cajeros["Eva Panel"]["total"]) == esperado_eva, f'{cajeros["Eva Panel"]["total"]} vs {esperado_eva}')
check("el código de empleado viaja en el corte", cajeros["Eva Panel"]["code"] == eva_codigo)
check("lo que suma cada cajero cuadra con el total del turno",
      sum(D(x["total"]) for x in cur["summary"]["by_cashier"]) == D(cur["summary"]["gross"])
      and sum(x["count"] for x in cur["summary"]["by_cashier"]) == cur["summary"]["sales_count"])
nombre_dueno = rep[v3.json()["order_id"]]["cashier_name"]
c0, t0 = base_cajeros.get(nombre_dueno, (0, D(0)))
check("la venta cancelada no cuenta en el corte: el dueño suma solo su venta vigente",
      cajeros[nombre_dueno]["count"] - c0 == 1 and D(cajeros[nombre_dueno]["total"]) - t0 == D(v3.json()["total"]),
      f'{cajeros[nombre_dueno]["count"] - c0} ventas, {D(cajeros[nombre_dueno]["total"]) - t0} vs {v3.json()["total"]}')
check("va de quien más cobró a quien menos",
      [D(x["total"]) for x in cur["summary"]["by_cashier"]] == sorted((D(x["total"]) for x in cur["summary"]["by_cashier"]), reverse=True))
cierre = c.post("/api/v1/cash/close", headers=dueno, json={"counted_cash": "0"})
check("al cerrar el turno el corte congela a los cajeros", cierre.status_code == 200 and any(x["name"] == "Eva Panel" for x in cierre.json()["summary"]["by_cashier"]), cierre.text[:150])
hist = c.get("/api/v1/cash/sessions?page_size=1", headers=dueno).json()["items"][0]
check("y el historial lo conserva", hist["status"] == "closed" and any(x["name"] == "Eva Panel" for x in hist["summary"]["by_cashier"]))

print("4) El Dashboard cuenta con los días del negocio")
# Las 4 ventas de la prueba se mueven a ayer a las 03:30 UTC: en México (UTC-6) eso es ANTES de las 9:30 pm de anteayer.
momento = datetime(ayer.year, ayer.month, ayer.day, 3, 30, tzinfo=timezone.utc)
conn.execute(text("update orders set created_at = :t where id = any(:ids)"), {"t": momento, "ids": ids})
d0, d360 = stats(dueno, 0), stats(dueno, 360)
dia_utc = ayer.isoformat()
dia_mx = (ayer - timedelta(days=1)).isoformat()
b0, b360, a0, a360 = por_fecha(base0), por_fecha(base360), por_fecha(d0), por_fecha(d360)
check("la venta cancelada tampoco cuenta en las gráficas (cuenta 4, no 5)", a0[dia_utc][1] - b0.get(dia_utc, (D(0), 0))[1] == 4)
check("sin desfase (UTC), las ventas caen en ese día", a0[dia_utc][1] - b0.get(dia_utc, (D(0), 0))[1] == 4, f"{a0[dia_utc]} vs {b0.get(dia_utc)}")
check("con el desfase de México (360 min) caen en el día ANTERIOR", a360[dia_mx][1] - b360.get(dia_mx, (D(0), 0))[1] == 4, f"{a360[dia_mx]} vs {b360.get(dia_mx)}")
check("y ese día de México no recibe las de UTC", a360.get(dia_utc, (D(0), 0))[1] == b360.get(dia_utc, (D(0), 0))[1])
check("el importe del día cuadra con lo cobrado", a0[dia_utc][0] - b0.get(dia_utc, (D(0), 0))[0] == total_mio, f"{a0[dia_utc][0] - b0.get(dia_utc, (D(0), 0))[0]} vs {total_mio}")
check("la gráfica trae 7 días seguidos, del más viejo a hoy", len(d360["days"]) == 7 and d360["days"] == sorted(d360["days"], key=lambda x: x["date"]))
check("los días sin ventas vienen en cero (no se omiten)", all("revenue" in d and "orders" in d for d in d360["days"]))
check("el total del periodo es la suma de los días", D(d360["total_revenue"]) == sum(D(d["revenue"]) for d in d360["days"]) and d360["total_orders"] == sum(d["orders"] for d in d360["days"]))
check("el ticket promedio es el total entre las ventas", d360["total_orders"] > 0 and D(d360["average_ticket"]) == (D(d360["total_revenue"]) / d360["total_orders"]).quantize(D("0.01")))
una = stats(dueno, 360, days=1)
check("con un solo día, solo cuenta hoy", len(una["days"]) == 1)

print("5) Lo más vendido")
top = d0["top_products"]
check("hay a lo más 5, de más a menos unidades", 1 <= len(top) <= 5 and [t["units"] for t in top] == sorted((t["units"] for t in top), reverse=True), str(top))
desde = datetime(ayer.year, ayer.month, ayer.day, tzinfo=timezone.utc) - timedelta(days=5)
for t in top[:3]:
    esperado = conn.execute(text(
        "select coalesce(sum(oi.quantity),0) from order_items oi join orders o on o.id = oi.order_id "
        "where o.tenant_id = (select tenant_id from users where email = 'admin@sabor-norteno.com') "
        "and o.status in ('paid','fulfilled') and o.created_at >= :d and oi.product_id = :p"),
        {"d": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6), "p": t["product_id"]}).scalar()
    check(f"'{t['name']}': las unidades coinciden con la base", int(esperado) == t["units"], f"{esperado} vs {t['units']}")

todos = {
    r[0]: int(r[1]) for r in conn.execute(text(
        "select oi.product_id, sum(oi.quantity) from order_items oi join orders o on o.id = oi.order_id "
        "where o.tenant_id = (select tenant_id from users where email = 'admin@sabor-norteno.com') "
        "and o.status in ('paid','fulfilled') and o.created_at >= :d group by oi.product_id"),
        {"d": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6)})
}
en_top = {t["product_id"] for t in top}
check("lo más vendido es de verdad lo más vendido de todo el negocio (no solo está ordenado)",
      top[0]["units"] == max(todos.values()) and all(u <= min(t["units"] for t in top) for pid, u in todos.items() if pid not in en_top),
      f"mejor del negocio {max(todos.values())}; en la lista {[t['units'] for t in top]}")

print("6) Cada negocio ve lo suyo")
op = stats(otro_dueno, 360)
check("la óptica no ve las ventas de Sabor Norteño", op["total_revenue"] == base_optica["total_revenue"] and op["total_orders"] == base_optica["total_orders"])
check("ni sus productos más vendidos", not ({P1, P2} & {t["product_id"] for t in op["top_products"]}))
check("un empleado no ve las estadísticas → 403", c.get(STATS, headers=eva).status_code == 403)
check("sin sesión → 401", c.get(STATS).status_code == 401)
check("días fuera de rango → 422", c.get(STATS, headers=dueno, params={"days": 0}).status_code == 422 and c.get(STATS, headers=dueno, params={"days": 32}).status_code == 422)
check("desfase fuera de rango → 422", c.get(STATS, headers=dueno, params={"tz_offset": 900}).status_code == 422)

print("7) Los mensajes están en español")
mal = c.post("/api/v1/auth/login", json={"email": "admin@sabor-norteno.com", "password": "mala-contraseña-1"})
check("contraseña incorrecta → 'Correo o contraseña incorrectos'", mal.status_code == 401 and mal.json().get("error") == "Correo o contraseña incorrectos", mal.text[:100])
nf = c.get("/api/v1/products/p_inexistente_000", headers=dueno)
check("producto inexistente → 'Producto no encontrado'", nf.status_code == 404 and nf.json().get("error") == "Producto no encontrado", nf.text[:100])
rep_correo = c.post("/api/v1/team", headers=dueno, json={"full_name": "Otra", "email": "eva.panel@example.com", "password": "Segura12345", "position": "empleado"})
check("correo repetido → mensaje en español", rep_correo.status_code == 400 and "ya está registrado" in (rep_correo.json().get("error") or ""), rep_correo.text[:120])

print("8) Reversión")
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
