"""
Prueba de punta a punta de la caja contra la base REAL (Neon), sin dejar rastro.

Toda la prueba corre dentro de una transacción exterior que se REVIERTE al
final: los `commit()` de los servicios se vuelven puntos de guardado, así que
ninguna venta, turno ni descuento de inventario sobrevive. Al terminar se
comprueba que los conteos de la base quedaron iguales.

No lo recoge pytest (no se llama `test_*`) porque necesita la base real y el
negocio de demostración (`seeders/seed_demo_tenants.py`). Se corre a mano:

    PYTHONPATH=. python tests/integracion/e2e_caja.py
"""
import sys
from decimal import Decimal as D

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from db.database import engine, get_db
from main import app

# El bcrypt del equipo es más nuevo de lo que passlib soporta (el contenedor
# fija un par compatible). Solo para esta prueba se verifica con bcrypt directo.
import bcrypt
import auth.service as _auth_service
_auth_service.verify_password = lambda plain, hashed: bcrypt.checkpw(plain.encode()[:72], hashed.encode())

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
        "select (select count(*) from orders), (select count(*) from cash_sessions), "
        "(select count(*) from products), (select coalesce(sum(inventory_count),0) from products)")).one()
    return tuple(int(x) for x in r)


antes = cuenta()
print(f"Estado inicial (pedidos, turnos, productos, unidades en inventario): {antes}\n")

tok = c.post("/api/v1/auth/login", json={"email": "admin@sabor-norteno.com", "password": "testpass123!"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

# Un producto final con existencias de sobra
prods = c.get("/api/v1/products?product_type=final&page_size=100", headers=H).json()["items"]
p = next(x for x in prods if x["inventory_count"] >= 30 and D(x["price"]) > 0)
precio = D(p["price"])
print(f"Producto de prueba: {p['name']} (${precio}, stock {p['inventory_count']})\n")

linea = lambda n: [{"product_id": p["product_id"], "quantity": n}]
stock = lambda: c.get(f"/api/v1/products/{p['product_id']}", headers=H).json()["inventory_count"]

print("1) Caja cerrada")
r = c.post("/api/v1/orders/business/sales", headers=H, json={"items": linea(1)})
check("cobrar sin caja → 409 CASH_CLOSED", r.status_code == 409 and r.json().get("code") == "CASH_CLOSED", r.text[:120])
check("sin turno abierto, /current devuelve null", c.get("/api/v1/cash/current", headers=H).json() is None)

print("2) Abrir caja")
r = c.post("/api/v1/cash/open", headers=H, json={"opening_float": "500"})
check("abrir con fondo de $500 → 201", r.status_code == 201 and r.json()["status"] == "open", r.text[:120])
sid = r.json()["session_id"]
r2 = c.post("/api/v1/cash/open", headers=H, json={"opening_float": "100"})
check("abrir otra vez → 409 CASH_ALREADY_OPEN", r2.status_code == 409 and r2.json().get("code") == "CASH_ALREADY_OPEN", r2.text[:120])

print("3) Cobros")
s0 = stock()
# Efectivo: 2 unidades, 10 % de descuento, $15 de propina, el cliente entrega $1000
sub = precio * 2
desc = (sub * D("10") / 100).quantize(D("0.01"), rounding="ROUND_HALF_UP")
total = sub - desc + D("15")
r = c.post("/api/v1/orders/business/sales", headers=H, json={
    "items": linea(2), "payment_method": "cash",
    "discount": {"type": "percent", "value": "10"}, "tip": "15", "amount_received": "1000"})
j = r.json()
check("venta en efectivo → 201", r.status_code == 201, r.text[:200])
check("subtotal, descuento, propina y total correctos",
      (D(j["subtotal"]), D(j["discount_amount"]), D(j["tip_amount"]), D(j["total"])) == (sub, desc, D("15.00"), total),
      f"{j['subtotal']}/{j['discount_amount']}/{j['tip_amount']}/{j['total']} vs {sub}/{desc}/15/{total}")
check("cambio = recibido - total", D(j["change_given"]) == D("1000") - total, j.get("change_given"))
check("el ticket trae quién cobró", j.get("cashier") == "admin", str(j.get("cashier")))
check("el inventario bajó 2", stock() == s0 - 2, f"{s0} → {stock()}")

# Tarjeta con propina
r = c.post("/api/v1/orders/business/sales", headers=H, json={"items": linea(1), "payment_method": "card", "tip": "10"})
check("venta con tarjeta + propina → 201", r.status_code == 201, r.text[:200])
check("tarjeta no guarda cambio", r.json()["change_given"] is None)
total_tarjeta = D(r.json()["total"])

# Efectivo exacto, sin descuento
r = c.post("/api/v1/orders/business/sales", headers=H, json={"items": linea(1), "payment_method": "cash", "amount_received": str(precio)})
check("efectivo exacto → cambio 0", r.status_code == 201 and D(r.json()["change_given"]) == 0, r.text[:200])
total_exacto = D(r.json()["total"])

print("4) Rechazos (no deben mover inventario)")
s1 = stock()
r = c.post("/api/v1/orders/business/sales", headers=H, json={"items": linea(1), "discount": {"type": "amount", "value": str(precio + 1)}})
check("descuento mayor al subtotal → 400 INVALID_DISCOUNT", r.status_code == 400 and r.json().get("code") == "INVALID_DISCOUNT", r.text[:150])
r = c.post("/api/v1/orders/business/sales", headers=H, json={"items": linea(1), "payment_method": "cash", "amount_received": "1"})
check("efectivo insuficiente → 400 INSUFFICIENT_PAYMENT", r.status_code == 400 and r.json().get("code") == "INSUFFICIENT_PAYMENT", r.text[:150])
r = c.post("/api/v1/orders/business/sales", headers=H, json={"items": linea(1), "payment_method": "card", "amount_received": "50"})
check("monto recibido con tarjeta → 422", r.status_code == 422, r.text[:120])
check("el inventario no se movió con los rechazos", stock() == s1, f"{s1} → {stock()}")

print("5) Cifras en vivo del turno")
cur = c.get("/api/v1/cash/current", headers=H).json()
cash_total = total + total_exacto
check("efectivo esperado = fondo + ventas en efectivo",
      D(cur["expected_cash"]) == D("500") + cash_total, f"{cur['expected_cash']} vs {D('500') + cash_total}")
check("3 ventas, 2 en efectivo y 1 con tarjeta",
      (cur["summary"]["sales_count"], cur["summary"]["cash"]["count"], cur["summary"]["card"]["count"]) == (3, 2, 1),
      str(cur["summary"]["sales_count"]))
check("descuentos y propinas del turno",
      (D(cur["summary"]["discounts"]), D(cur["summary"]["tips_total"]), D(cur["summary"]["tips_cash"])) == (desc, D("25.00"), D("15.00")),
      f"{cur['summary']['discounts']}/{cur['summary']['tips_total']}/{cur['summary']['tips_cash']}")

print("6) Una venta cancelada sale del corte")
ventas = c.get("/api/v1/orders/business/sales?page_size=5", headers=H).json()
cancelada = next(v for v in ventas["items"] if v["payment_method"] == "card")
c.patch(f"/api/v1/orders/business/sales/{cancelada['order_id']}/status", headers=H, json={"status": "canceled"})
cur = c.get("/api/v1/cash/current", headers=H).json()
check("la cancelada ya no cuenta ni infla el efectivo",
      cur["summary"]["sales_count"] == 2 and cur["summary"]["excluded_count"] == 1 and D(cur["expected_cash"]) == D("500") + cash_total,
      str(cur["summary"]))

print("7) Reporte de ventas")
rep = c.get("/api/v1/orders/business/sales?page_size=50", headers=H).json()
check("el resumen trae propinas y descuentos (sin contar la cancelada)",
      D(rep["summary"]["discounts"]) >= desc and D(rep["summary"]["tips"]) >= D("15.00"), str(rep["summary"]))

print("8) Cierre y diferencia")
esperado = D("500") + cash_total
r = c.post("/api/v1/cash/close", headers=H, json={"counted_cash": str(esperado - 30), "notes": "Prueba de corte"})
j = r.json()
check("cerrar con $30 de menos → faltante de -30", r.status_code == 200 and D(j["difference"]) == D("-30.00"), r.text[:200])
check("queda cerrado y congelado con el esperado correcto", j["status"] == "closed" and D(j["expected_cash"]) == esperado)
check("sin turno abierto, cerrar otra vez → 404", c.post("/api/v1/cash/close", headers=H, json={"counted_cash": "0"}).status_code == 404)
check("tras cerrar, cobrar → 409 de nuevo", c.post("/api/v1/orders/business/sales", headers=H, json={"items": linea(1)}).status_code == 409)
hist = c.get("/api/v1/cash/sessions", headers=H).json()
check("el corte aparece en el historial", any(s["session_id"] == sid for s in hist["items"]))
check("se puede leer el corte por id", c.get(f"/api/v1/cash/sessions/{sid}", headers=H).status_code == 200)
check("un id inexistente → 404", c.get("/api/v1/cash/sessions/cs_noexiste", headers=H).status_code == 404)

print("9) SKU y código de barras")
meta = {"categoria": "plato_fuerte", "tiempo_preparacion_min": 5, "picante": False}
r = c.post("/api/v1/products", headers=H, json={"product_type": "final", "name": "ZZ Prueba caja", "price": "10", "metadata": meta, "sku": "ZZ-CAJA-1", "barcode": "9990001112223"})
check("crear producto con SKU y código de barras → 201", r.status_code == 201 and r.json()["sku"] == "ZZ-CAJA-1", r.text[:200])
pid = r.json()["product_id"]
r = c.post("/api/v1/products", headers=H, json={"product_type": "final", "name": "ZZ Otro", "price": "10", "metadata": meta, "sku": "zz-caja-1"})
check("SKU repetido (otra capitalización) → 400", r.status_code == 400 and r.json().get("code") == "DUPLICATE_PRODUCT_CODE", r.text[:150])
r = c.post("/api/v1/products", headers=H, json={"product_type": "final", "name": "ZZ Otro", "price": "10", "metadata": meta, "barcode": "9990001112223"})
check("código de barras repetido → 400", r.status_code == 400 and r.json().get("code") == "DUPLICATE_PRODUCT_CODE", r.text[:150])
busq = lambda q: [x["name"] for x in c.get(f"/api/v1/products?search={q}", headers=H).json()["items"]]
check("buscar por SKU encuentra el producto", "ZZ Prueba caja" in busq("ZZ-CAJA"), str(busq("ZZ-CAJA")))
check("buscar por código de barras lo encuentra", "ZZ Prueba caja" in busq("9990001112223"), str(busq("9990001112223")))
r = c.patch(f"/api/v1/products/{pid}", headers=H, json={"sku": ""})
check("cadena vacía quita el SKU y conserva el código de barras", r.json()["sku"] is None and r.json()["barcode"] == "9990001112223", r.text[:200])
r = c.patch(f"/api/v1/products/{pid}", headers=H, json={"sku": "con espacio"})
check("SKU con formato inválido → 422", r.status_code == 422, r.text[:120])
sf = c.get("/s/sabor-norteno/products?page_size=100").json()
check("la tienda pública NO expone SKU ni código de barras",
      all("sku" not in i and "barcode" not in i for i in sf["items"]))

print("\n10) Reversión")
outer.rollback()
conn.close()
conn2 = engine.connect()
despues = tuple(int(x) for x in conn2.execute(text(
    "select (select count(*) from orders), (select count(*) from cash_sessions), "
    "(select count(*) from products), (select coalesce(sum(inventory_count),0) from products)")).one())
check(f"la base quedó EXACTAMENTE igual ({antes} → {despues})", despues == antes)

print(f"\n{'TODO EN ORDEN' if not fallos else str(len(fallos)) + ' FALLOS: ' + ', '.join(fallos)}")
sys.exit(1 if fallos else 0)
