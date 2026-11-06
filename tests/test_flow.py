"""Flujo punta a punta con Yango simulado: tienda → cotización → pedido → bot → estado."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings

ADMIN = {"x-admin-key": settings.admin_key}
NEAR = {"lat": -17.7700, "lon": -63.1550}   # ~1 km del local simulado
FAR = {"lat": -17.40, "lon": -63.00}        # fuera de zona


@pytest.fixture()
def client(fresh_db, monkeypatch):
    monkeypatch.setattr(settings, "demo_seed", True)
    monkeypatch.setattr(settings, "orders_per_ip", 100)
    from app import api
    api._ip_hits.clear()
    api.app.state.background = False
    with TestClient(api.app) as c:
        r = c.post("/api/admin/partners/wrap-roll/sync", headers=ADMIN)
        assert r.status_code == 200, r.text
        yield c


def cart_item(c):
    menu = c.get("/api/wrap-roll/menu").json()
    item = menu["items"]["3009756314"]  # Wrap M + gaseosa, Bs43 promo
    opts = {g["id"]: [g["options"][0]["id"]] for g in item["groups"] if g["required"] or g["min"]}
    return {"item_id": item["id"], "quantity": 1, "options": opts}


def order_body(c, **over):
    body = {
        "customer": {"first_name": "Juan", "last_name": "Pérez", "phone": "74275986"},
        "address": {**NEAR, "text": "Av. Busch", "reference": "Casa 24, portón negro", "comment": "tocar timbre"},
        "payment": {"method": "qr"},
        "items": [cart_item(c)],
    }
    body.update(over)
    return body


def wait_status(c, token, statuses, timeout=5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        o = c.get(f"/api/wrap-roll/orders/{token}").json()
        if o["status"] in statuses:
            return o
        time.sleep(0.05)
    raise AssertionError(f"estado {o['status']}")


def test_store_pages(client):
    assert client.get("/wrap-roll").status_code == 200
    assert "Wrap &amp; Roll" in client.get("/wrap-roll").text
    assert client.get("/no-existe").status_code == 404
    assert client.get("/wrap-roll/portal").status_code == 200


def test_quote_near_and_far(client):
    q = client.post("/api/wrap-roll/quote", json={**NEAR, "subtotal": 43}).json()
    # Menos de 1 km: tramo Bs5–10. Sin tarifa de servicio fija.
    assert q["available"] and q["can_order"] and q["fee_range"] == {"min": 5, "max": 10, "label": "Hasta 1 km"}
    assert q["distance_km"] < 1 and 5 <= q["delivery_fee"] <= 10 and q["total"] == 43 + q["delivery_fee"] and q["service_fee"] == 0
    mid = client.post("/api/wrap-roll/quote", json={"lat": -17.7833, "lon": -63.1250, "subtotal": 43}).json()
    assert mid["distance_km"] > 3 and mid["fee_range"]["min"] == 15 and 15 <= mid["delivery_fee"] <= 25
    far = client.post("/api/wrap-roll/quote", json={**FAR, "subtotal": 43}).json()
    assert not far["available"] and far["reason"] == "fuera_de_zona" and not far["can_order"]
    out = client.post("/api/wrap-roll/quote", json={"lat": 40.4, "lon": -3.7, "subtotal": 43}).json()
    assert out["reason"] == "fuera_de_zona"


def test_order_manual_when_placement_off(client, monkeypatch):
    monkeypatch.setattr(settings, "order_placement_enabled", False)
    r = client.post("/api/wrap-roll/orders", json=order_body(client))
    assert r.status_code == 200, r.text
    o = wait_status(client, r.json()["token"], {"manual"})
    fee = client.post("/api/wrap-roll/quote", json={**NEAR, "subtotal": 43}).json()["delivery_fee"]
    assert o["total"] == 43 + fee and o["payment"] == "QR al mensajero"
    full = client.get("/api/admin/orders", headers=ADMIN).json()[0]
    assert full["yango_comment"].startswith("CLIENTE: Juan Pérez | TEL: +591 74275986 | PAGO: QR al mensajero"
                                            f" | COBRAR: Bs{43 + fee:g}")
    assert full["commission"] == 1.29 and full["channel"] == "web" and full["yango_fee"] == 0
    assert "REF: Casa 24, portón negro" in full["yango_comment"] and "NOTA: tocar timbre" in full["yango_comment"]
    # Operador lo carga a mano en Yango y lo marca.
    r = client.post(f"/api/admin/orders/{full['token']}/status", headers=ADMIN,
                    json={"action": "colocado", "yango_order_nr": "261029-BO1234567"})
    assert r.json()["status"] == "colocado"


def test_order_placed_by_bot(client, monkeypatch):
    monkeypatch.setattr(settings, "order_placement_enabled", True)
    from app import runtime
    src = runtime.get_runtime().source
    r = client.post("/api/wrap-roll/orders", json=order_body(client, payment={"method": "cash", "change_for": 100}))
    o = wait_status(client, r.json()["token"], {"colocado"})
    assert o["status_text"].startswith("Pedido confirmado")
    assert src.placed[-1]["account"] == "cuenta_demo"
    assert "PAGO: Efectivo al mensajero (paga con Bs100)" in src.placed[-1]["comment"]


def test_bot_retries_then_manual(client, monkeypatch):
    monkeypatch.setattr(settings, "order_placement_enabled", True)
    monkeypatch.setattr(settings, "order_max_attempts", 2)
    from app import runtime
    rt = runtime.get_runtime()
    rt.dispatcher.retry_delays = [0]
    rt.source.fail_next = 1
    r = client.post("/api/wrap-roll/orders", json=order_body(client))
    assert wait_status(client, r.json()["token"], {"colocado"})["status"] == "colocado"
    rt.source.fail_next = 5
    r = client.post("/api/wrap-roll/orders", json=order_body(client))
    assert wait_status(client, r.json()["token"], {"manual"})["status"] == "manual"
    rt.source.fail_next = 0
    rt.source.mismatch_next = True
    r = client.post("/api/wrap-roll/orders", json=order_body(client))
    assert wait_status(client, r.json()["token"], {"revision"})["status"] == "revision"


def test_order_validation(client):
    bad = client.post("/api/wrap-roll/orders", json=order_body(client, address={**FAR, "text": "x y z", "reference": "casa 1"}))
    assert bad.status_code == 422 and "no entrega" in bad.json()["detail"]
    item = cart_item(client)
    bad = client.post("/api/wrap-roll/orders", json=order_body(client, items=[{**item, "options": {}}]))
    assert bad.status_code == 422
    bad = client.post("/api/wrap-roll/orders", json=order_body(client, customer={"first_name": "J", "last_name": "P", "phone": "1"}))
    assert bad.status_code == 422
    bad = client.post("/api/wrap-roll/orders", json=order_body(client, payment={"method": "card"}))
    assert bad.status_code == 422
    # El total que vio el cliente ya no coincide (precio manipulado o cambió el envío): no se crea.
    bad = client.post("/api/wrap-roll/orders", json=order_body(client, expected_total=1))
    assert bad.status_code == 422 and "cambió" in bad.json()["detail"]


def test_portal_theme_and_auth(client):
    assert client.put("/api/wrap-roll/portal/theme", json={"primary": "#000000"}).status_code == 401
    assert client.post("/api/wrap-roll/portal/login", json={"password": "mal"}).status_code == 401
    assert client.post("/api/wrap-roll/portal/login", json={"password": "demo12345"}).status_code == 200
    t = client.put("/api/wrap-roll/portal/theme", json={"primary": "#112233", "radius": 99, "card_layout": "list"}).json()
    assert t["primary"] == "#112233" and t["radius"] == 32 and t["card_layout"] == "list"
    assert client.put("/api/wrap-roll/portal/theme", json={"primary": "red;}body{display:none"}).status_code == 422
    assert client.put("/api/wrap-roll/portal/theme", json={"logo_url": "javascript:alert(1)"}).status_code == 422
    page = client.get("/wrap-roll").text
    assert "--primary:#112233" in page and "grid-layout" not in page
    client.put("/api/wrap-roll/portal/theme", json={"card_layout": "grid"})
    assert "grid-layout" in client.get("/wrap-roll").text
    client.put("/api/wrap-roll/portal/accepting", json={"accepting": False})
    r = client.post("/api/wrap-roll/orders", json=order_body(client))
    assert r.status_code == 422 and "no está recibiendo" in r.json()["detail"]


def test_admin_requires_key(client):
    assert client.get("/api/admin/orders").status_code == 401
    assert client.get("/api/admin/health", headers=ADMIN).status_code == 200


def test_session_import(client):
    cookies = [{"name": "Session_id", "value": "abc", "domain": ".yango.com", "path": "/", "secure": True,
                "httpOnly": True, "sameSite": "no_restriction", "expirationDate": 1893456000}]
    r = client.post("/api/admin/accounts/cuenta_demo/cookies", headers=ADMIN, content=json.dumps(cookies))
    assert r.status_code == 200 and r.json()["cookies"] == 1
    state = json.loads((settings.sessions_dir / "cuenta_demo.json").read_text())
    assert state["cookies"][0]["sameSite"] == "None" and state["cookies"][0]["expires"] == 1893456000
    bad = client.post("/api/admin/accounts/cuenta_demo/cookies", headers=ADMIN, content="[]")
    assert bad.status_code == 422
    bad = client.post("/api/admin/accounts/..%2Fx/cookies", headers=ADMIN, content=json.dumps(cookies))
    assert bad.status_code in (404, 422)


def test_quote_cache_and_dedupe():
    """Pico: 200 cotizaciones del mismo punto a la vez → 1 consulta a Yango."""
    from app.quotes import QuoteService
    from bot.mock import MockSource
    calls = []
    src = MockSource(latency=0.05)

    async def fetch(account, slug, lat, lon):
        calls.append((lat, lon))
        return await src.catalog(account, slug, lat, lon)

    async def main():
        qs = QuoteService(fetch)
        res = await asyncio.gather(*[qs.quote("a", "s", -17.77, -63.155) for _ in range(200)])
        assert all(r["available"] for r in res)
        await asyncio.gather(*[qs.quote("a", "s", -17.77 + i * 0.001, -63.155) for i in range(50)])
        return qs

    qs = asyncio.run(main())
    assert len(calls) == 50  # el primer punto se repite en el segundo lote (i=0)
    assert qs.stats["misses"] == 50


def test_store_shows_delivery_tiers(client):
    page = client.get("/wrap-roll").text
    assert "Envío Bs5–Bs25" in page and "Hasta 1 km" in page and "Más de 3 km" in page


def test_theme_font_size_and_thumbs_reach_css(client):
    """El tamaño de letra va en <html> (mueve los rem) y la foto de la lista sigue tamaño y forma."""
    client.post("/api/wrap-roll/portal/login", json={"password": "demo12345"})
    client.put("/api/wrap-roll/portal/theme", json={"base_font_size": 19, "card_size": "l", "image_ratio": "16/9"})
    page = client.get("/wrap-roll").text
    html_tag = page.split("<html", 1)[1].split(">", 1)[0]
    assert "--base-size:19px" in html_tag and "--thumb:150px" in html_tag and "--img-ratio:16/9" in html_tag


def test_chain_one_bot_and_login(client):
    c = client.post("/api/admin/chains", headers=ADMIN, json={
        "slug": "wraps-bo", "name": "Wraps Bolivia", "account": "bot_wraps", "login": "wraps",
        "password": "cadena1234", "partners": ["wrap-roll"]})
    assert c.status_code == 200, c.text
    cid = c.json()["id"]
    p2 = client.post("/api/admin/partners", headers=ADMIN, json={"slug": "wrap-roll-2", "name": "Wrap & Roll Equipetrol",
                                                                  "chain_id": cid, "account": "otra"}).json()
    assert p2["account"] == "bot_wraps"  # el bot es de la cadena
    parts = {p["slug"]: p for p in client.get("/api/admin/partners", headers=ADMIN).json()}
    assert parts["wrap-roll"]["account"] == "bot_wraps" and parts["wrap-roll"]["chain_id"] == cid
    # Cambiar el bot de la cadena mueve a todos sus locales.
    client.put(f"/api/admin/chains/{cid}", headers=ADMIN, json={"account": "bot_wraps_2"})
    parts = {p["slug"]: p for p in client.get("/api/admin/partners", headers=ADMIN).json()}
    assert {parts["wrap-roll"]["account"], parts["wrap-roll-2"]["account"]} == {"bot_wraps_2"}
    # Login de la cadena: ve sus locales y entra a su portal, no al de otros.
    client.cookies.clear()
    assert client.get("/api/cadena/me").status_code == 401
    assert client.post("/api/cadena/login", json={"login": "wraps", "password": "mal"}).status_code == 401
    assert client.post("/api/cadena/login", json={"login": "wraps", "password": "cadena1234"}).status_code == 200
    me = client.get("/api/cadena/me").json()
    assert {p["slug"] for p in me["locales"]} == {"wrap-roll", "wrap-roll-2"}
    assert client.put("/api/wrap-roll/portal/theme", json={"primary": "#123456"}).status_code == 200
    client.post("/api/admin/partners", headers=ADMIN, json={"slug": "otro", "name": "Otro", "account": "otro"})
    assert client.put("/api/otro/portal/theme", json={"primary": "#123456"}).status_code == 401
    r = client.post("/api/admin/chains", headers=ADMIN, json={"slug": "x", "name": "X", "account": "bot_wraps_2",
                                                              "login": "x1", "password": "12345678"})
    assert r.status_code == 422  # una cuenta (bot) no se comparte entre cadenas


def test_operator_phone_order_with_script(client):
    assert client.get("/api/operador/me").status_code == 401
    r = client.post("/api/admin/operators", headers=ADMIN, json={"login": "ana", "name": "Ana", "password": "operadora1"})
    assert r.status_code == 200, r.text
    assert client.post("/api/operador/login", json={"login": "ana", "password": "operadora1"}).status_code == 200
    me = client.get("/api/operador/me").json()
    assert me["operator"]["name"] == "Ana" and me["script"][0]["id"] == "saludo" and me["partners"]
    call = client.post("/api/operador/calls", json={"slug": "wrap-roll"}).json()
    # Pedido telefónico: el operador no manda precios; el servidor recalcula y valida igual que en la web.
    body = order_body(client)
    body["slug"] = "wrap-roll"
    r = client.post("/api/operador/orders", json=body)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    full = next(o for o in client.get("/api/admin/orders", headers=ADMIN).json() if o["token"] == token)
    assert full["channel"] == "telefono" and full["operator"] == "ana" and "| TEL #" in full["yango_comment"]
    bad = client.post("/api/operador/orders", json={**body, "expected_total": 1})
    assert bad.status_code == 422
    # Cliente conocido por su celular: nombre y última dirección.
    k = client.get("/api/operador/customer?phone=74275986").json()
    assert k["known"] and k["customer"]["first_name"] == "Juan" and k["addresses"][0]["reference"] == "Casa 24, portón negro"
    assert client.get("/api/operador/customer?phone=123").status_code == 422
    end = client.put(f"/api/operador/calls/{call['id']}", json={"outcome": "pedido", "notes": "ok", "order_token": token})
    assert end.status_code == 200 and end.json()["outcome"] == "pedido"
    stats = client.get("/api/admin/operators", headers=ADMIN).json()["today"]
    assert stats[0]["operator"] == "ana" and stats[0]["orders"] == 1
    # Operador desactivado: la sesión deja de servir.
    client.put("/api/admin/operators/ana", headers=ADMIN, json={"enabled": False})
    assert client.get("/api/operador/me").status_code == 401


def test_call_script_editable(client):
    r = client.put("/api/admin/call-script", headers=ADMIN, json={"steps": [{"title": "Hola", "say": "Hola {cliente}"}]})
    assert r.status_code == 200 and r.json()["steps"][0]["id"] == "paso1"
    assert client.put("/api/admin/call-script", headers=ADMIN, json={"steps": [{"title": "", "say": ""}]}).status_code == 422
    client.put("/api/admin/call-script", headers=ADMIN, json={"steps": "default"})
    assert client.get("/api/admin/call-script", headers=ADMIN).json()["steps"][0]["id"] == "saludo"


def test_pdf_menu_editor(client):
    assert client.get("/api/admin/pdf/wrap-roll").status_code == 401
    d = client.get("/api/admin/pdf/wrap-roll", headers=ADMIN).json()
    assert d["has_menu"] and d["style"]["template"] == "clasico" and d["categories"]
    hide = d["categories"][0]["key"]
    st = client.put("/api/admin/pdf/wrap-roll", headers=ADMIN, json={"template": "pizarra", "apply_template": True}).json()
    assert st["template"] == "pizarra" and st["heading_font"] == "Bebas Neue"
    st = client.put("/api/admin/pdf/wrap-roll", headers=ADMIN, json={"columns": 3, "title": "Carta",
                                                                      "hidden_categories": [hide]}).json()
    assert st["columns"] == 3 and st["heading_font"] == "Bebas Neue"
    assert client.put("/api/admin/pdf/wrap-roll", headers=ADMIN, json={"primary": "x"}).status_code == 422
    html = client.get("/admin/pdf/wrap-roll/preview", headers=ADMIN).text
    assert "tpl-pizarra" in html and "--cols:3" in html and ">Carta<" in html and "<svg" in html  # QR
    assert f">{d['categories'][0]['name']}<" not in html
    assert "Wrap M + gaseosa" in html or "Wrap" in html
    assert client.get("/admin/pdf/wrap-roll/preview").status_code == 401


def test_commission_report(client):
    client.post("/api/wrap-roll/orders", json=order_body(client))
    rep = client.get("/api/admin/commissions", headers=ADMIN).json()
    assert rep["rate"] == 0.03 and rep["rows"][0]["commission"] == 1.29 and rep["rows"][0]["orders"] == 1
    h = client.get("/api/admin/health", headers=ADMIN).json()
    assert h["commission_month"] == 1.29 and len(h["delivery_tiers"]) == 3
