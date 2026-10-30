"""Seguimiento del pedido (vendor app del local o cuenta cliente) y avisos Web Push al cliente."""
import json

from app import push, tracking
from app.config import settings
from tests.test_flow import ADMIN, client, order_body, wait_status  # noqa: F401

# Estructura verificada de la vendor app (restotec-yango, docs/VENDOR_APP.md).
ACTIVE = {"orders_by_status": [
    {"status": "new", "orders": [{"order_nr": "261030-BO1", "status": "new", "courier_approximate_arrival_time": None}]},
    {"status": "accepted", "orders": [{"order_nr": "261030-BO2", "status": "accepted"}]},
    {"status": "ready", "orders": []},
    {"status": "in_delivery", "orders": [{"order_nr": "261030-BO3", "status": "in_delivery",
                                          "courier_approximate_arrival_time": "2026-10-30T20:10:00+00:00"}]},
]}


def test_stage_mapping():
    assert [tracking.stage_of(s) for s in ("new", "accepted", "ready", "in_delivery", "given", "released", "refused")] == [
        "recibido", "preparando", "listo", "en_camino", "en_camino", "entregado", "cancelado"]
    assert tracking.stage_of("courier_assigned") == "en_camino"
    assert tracking.stage_of("") is None and tracking.stage_of("zzz") is None


def test_parse_vendor():
    a = tracking.parse_vendor_active(ACTIVE)
    assert {k: v["stage"] for k, v in a.items()} == {"261030-BO1": "recibido", "261030-BO2": "preparando",
                                                     "261030-BO3": "en_camino"}
    assert a["261030-BO3"]["eta"].startswith("2026-10-30T20:10")
    assert tracking.parse_vendor_active({"nada": 1}) == {} and tracking.parse_vendor_active(None) == {}
    assert tracking.parse_vendor_final({"status": "refused", "cancel_reason": {"title": "x"}})["stage"] == "cancelado"
    assert tracking.parse_vendor_final({"order_nr": "x"}) is None


def test_find_status_generic():
    body = {"payload": {"trackedOrders": [{"order": {"orderNr": "261030-BO9"}, "status": {"id": "taken"}},
                                          {"order": {"orderNr": "261030-BO8"}, "status": {"id": "cooking"}}]}}
    assert tracking.find_status(body, "261030-BO9") == "taken"
    assert tracking.find_status(body, "261030-BO8") == "cooking"
    assert tracking.find_status({"orders": [{"order_nr": "A1", "status": "delivered"}]}, "A1") == "delivered"
    assert tracking.find_status(body, "nope") is None


def _placed(client, monkeypatch):
    monkeypatch.setattr(settings, "order_placement_enabled", True)
    r = client.post("/api/wrap-roll/orders", json=order_body(client))
    token = r.json()["token"]
    wait_status(client, token, {"colocado"})
    return token


def test_vendor_progress_and_push(client, monkeypatch):
    from app import orders, runtime
    rt = runtime.get_runtime()
    sent = []
    monkeypatch.setattr(push, "sender", lambda sub, payload: sent.append(json.loads(payload)))
    assert client.put("/api/admin/partners/wrap-roll", json={"vendor_account": "vendor wr"}, headers=ADMIN).status_code == 422
    assert client.put("/api/admin/partners/wrap-roll", json={"vendor_account": "vendor_wr"}, headers=ADMIN).status_code == 200
    token = _placed(client, monkeypatch)
    nr = orders.get(token)["yango_order_nr"]

    key = client.get("/api/push/key").json()["key"]
    assert len(key) > 80
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPk" + "x" * 80, "auth": "y" * 22}}
    assert client.post(f"/api/wrap-roll/orders/{token}/push", json={"endpoint": "http://x"}).status_code == 422
    assert client.post(f"/api/wrap-roll/orders/{token}/push", json=sub).json()["subscriptions"] == 1

    def run():
        return client.portal.call(tracking.poll_once, rt.source)
    for raw, stage in (("new", "recibido"), ("accepted", "preparando"), ("in_delivery", "en_camino")):
        rt.source.vendor_status[nr] = raw
        assert run() == 1
        o = client.get(f"/api/wrap-roll/orders/{token}").json()
        assert o["stage"] == stage
    assert o["status_text"] == "Tu pedido va en camino"
    rt.source.vendor_status[nr] = "accepted"  # nunca retrocede
    assert run() == 0
    rt.source.vendor_status[nr] = "released"  # salió de activos: el detalle dice que se entregó
    assert run() == 1
    assert client.get(f"/api/wrap-roll/orders/{token}").json()["stage"] == "entregado"
    assert run() == 0  # entregado: ya no se sigue
    assert [s["title"].split(" · ")[1] for s in sent] == ["El restaurante recibió tu pedido", "Están preparando tu pedido",
                                                         "Tu pedido va en camino", "Pedido entregado"]
    assert all(s["url"] == f"/wrap-roll/pedido/{token}" for s in sent)


def test_customer_account_tracking(client, monkeypatch):
    from app import orders, runtime
    rt = runtime.get_runtime()
    token = _placed(client, monkeypatch)
    nr = orders.get(token)["yango_order_nr"]
    rt.source.vendor_status[nr] = "refused"
    assert client.portal.call(tracking.poll_once, rt.source) == 1
    o = client.get(f"/api/wrap-roll/orders/{token}").json()
    assert o["stage"] == "cancelado" and o["status_text"] == "Pedido cancelado"


def test_pwa_files(client):
    from app import orders
    r = client.get("/sw.js")
    assert r.status_code == 200 and r.headers["service-worker-allowed"] == "/" and "showNotification" in r.text
    m = client.get("/wrap-roll/manifest.webmanifest").json()
    assert m["start_url"] == "/wrap-roll" and m["display"] == "standalone" and m["icons"]
    token = client.post("/api/wrap-roll/orders", json=order_body(client)).json()["token"]
    assert client.get(f"/wrap-roll/manifest.webmanifest?order={token}").json()["start_url"] == f"/wrap-roll/pedido/{token}"
    assert client.get("/wrap-roll/manifest.webmanifest?order=../x").json()["start_url"] == "/wrap-roll"
    assert client.get("/wrap-roll/icon.svg").text.startswith("<svg")
    page = client.get(f"/wrap-roll/pedido/{token}").text
    assert "manifest.webmanifest?order=" in page and "Agregar a inicio" in page and "Activar avisos" in page
    assert orders.get(token)["track_stage"] is None
