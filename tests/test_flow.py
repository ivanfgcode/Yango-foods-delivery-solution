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
    assert q["available"] and q["delivery_fee"] == 0 and q["can_order"] and q["total"] == 46
    far = client.post("/api/wrap-roll/quote", json={**FAR, "subtotal": 43}).json()
    assert not far["available"] and far["reason"] == "fuera_de_zona" and not far["can_order"]
    out = client.post("/api/wrap-roll/quote", json={"lat": 40.4, "lon": -3.7, "subtotal": 43}).json()
    assert out["reason"] == "fuera_de_zona"


def test_quote_zone_closed_vs_restaurant_closed(client, monkeypatch):
    from app import runtime
    src = runtime.get_runtime().source
    runtime.get_runtime().quotes._cache.clear()
    monkeypatch.setattr(src, "zone_closed_beyond_km", 0.5)  # NEAR está a ~1 km: zona cerrada, el local abierto
    q = client.post("/api/wrap-roll/quote", json={**NEAR, "subtotal": 43}).json()
    assert q["reason"] == "zona_cerrada" and not q["can_order"]
    runtime.get_runtime().quotes._cache.clear()
    monkeypatch.setattr(src, "closed", True)  # el local mismo no atiende: cerrado de verdad
    q = client.post("/api/wrap-roll/quote", json={**NEAR, "subtotal": 43}).json()
    assert q["reason"] == "cerrado" and "opens_at" in q


def test_store_shows_branch_and_address_search(client):
    page = client.get("/wrap-roll").text
    assert '"branch": {"name": "' in page and 'id="branchLine"' in page
    r = client.post("/api/wrap-roll/address-search", json={"q": "Rafael Peña 100"}).json()["results"]
    assert r and r[0]["title"] == "Rafael Peña, 100"
    assert client.post("/api/wrap-roll/address-search", json={"q": "ab"}).json() == {"results": []}


def test_order_manual_when_placement_off(client, monkeypatch):
    monkeypatch.setattr(settings, "order_placement_enabled", False)
    r = client.post("/api/wrap-roll/orders", json=order_body(client))
    assert r.status_code == 200, r.text
    o = wait_status(client, r.json()["token"], {"manual"})
    assert o["total"] == 46 and o["payment"] == "QR al mensajero"
    full = client.get("/api/admin/orders", headers=ADMIN).json()[0]
    assert full["yango_comment"].startswith("CLIENTE: Juan Pérez | TEL: +591 74275986 | PAGO: QR al mensajero")
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
    assert bad.status_code == 422 and "no llegamos" in bad.json()["detail"]
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


def test_session_import_cookie_header(client):
    r = client.post("/api/admin/accounts/cuenta_demo/cookies", headers=ADMIN,
                    content="Cookie: Session_id=3:abc.def; yandexuid=123; i=x/y==")
    assert r.status_code == 200 and r.json()["cookies"] == 3
    state = json.loads((settings.sessions_dir / "cuenta_demo.json").read_text())
    assert {c["name"]: c["value"] for c in state["cookies"]}["i"] == "x/y=="
    assert all(c["domain"] == ".yango.com" for c in state["cookies"])
    bad = client.post("/api/admin/accounts/cuenta_demo/cookies", headers=ADMIN, content="hola que tal")
    assert bad.status_code == 422


def test_dashboard_summary(client, monkeypatch):
    monkeypatch.setattr(settings, "order_placement_enabled", False)
    for _ in range(2):
        assert client.post("/api/wrap-roll/orders", json=order_body(client)).status_code == 200
    assert client.get("/api/admin/dashboard").status_code == 401
    d = client.get("/api/admin/dashboard?days=7", headers=ADMIN).json()
    assert d["orders"] == 2 and d["pending"] == 2 and d["status"] == {"manual": 2}
    assert d["revenue"] == round(d["avg_ticket"] * 2, 2) and d["revenue"] > d["delivery"]
    assert sum(d["hourly"]) == 2 and len(d["weekday"]) == 7 and d["daily"][0]["orders"] == 2
    assert d["top"][0]["units"] == 2 and d["partners"][0]["name"] == "Wrap & Roll"
    assert d["payments"] == {"qr": 2}
    other = client.get("/api/admin/dashboard?partner=no-existe&days=0", headers=ADMIN).json()
    assert other["orders"] == 2  # restaurante desconocido = todos


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
