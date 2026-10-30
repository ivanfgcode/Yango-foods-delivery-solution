"""Upselling por compras conjuntas y videos de TikTok etiquetados con producto."""
import json

import pytest

from app import menu as menu_mod, tiktok, upsell
from app.config import settings
from tests.test_flow import ADMIN, NEAR, client  # noqa: F401  (fixture)



@pytest.fixture()
def menu():
    return menu_mod.parse_menu_retrieve(json.loads((settings_root() / "menu_retrieve.json").read_text()))


def settings_root():
    from app.config import ROOT
    return ROOT / "tests" / "fixtures"


def simple_items(m, n):
    """Productos sin opciones obligatorias que no son bebidas."""
    drinks = set(upsell.drink_ids(m))
    return [i for i, it in m["items"].items() if it["available"] and i not in drinks
            and not any(g["required"] or g["min"] for g in it["groups"])][:n]


def test_drinks_detected_by_type_not_brand(menu):
    names = [menu["items"][i]["name"] for i in upsell.drink_ids(menu)]
    assert names[0] == "Coca-Cola" and "Aquarius pomelo" in names
    assert "Wrap M + gaseosa" not in names  # «Combos Coca-Cola» es un combo, no una bebida


def test_cold_start_suggests_drinks(fresh_db, menu):
    upsell.clear_cache()
    a = simple_items(menu, 1)[0] if simple_items(menu, 1) else next(iter(menu["items"]))
    recs = upsell.suggest(999, menu, [a], "item")
    assert recs and all(r["reason"] == "bebida" for r in recs)
    drink = upsell.drink_ids(menu)[0]
    assert upsell.suggest(999, menu, [a, drink], "cart") == []  # ya lleva bebida y no hay historial


def order_body_items(c, ids):
    m = c.get("/api/wrap-roll/menu").json()
    lines = []
    for i in ids:
        it = m["items"][i]
        opts = {g["id"]: [g["options"][0]["id"]] for g in it["groups"] if g["required"] or g["min"]}
        lines.append({"item_id": i, "quantity": 1, "options": opts})
    return {"customer": {"first_name": "Juan", "last_name": "Pérez", "phone": "74275986"},
            "address": {**NEAR, "text": "Av. Busch", "reference": "Casa 24"}, "payment": {"method": "cash"},
            "items": lines}


def test_bought_together_wins_over_drinks(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "order_placement_enabled", False)
    m = menu_mod.load(1)
    a, b, c3 = simple_items(m, 3)
    for ids in ([a, b], [a, b], [a, c3]):
        assert client.post("/api/wrap-roll/orders", json=order_body_items(client, ids)).status_code == 200
    recs = client.get(f"/api/wrap-roll/upsell?items={a}").json()["items"]
    assert recs[0] == {"item_id": b, "reason": "combinado", "pct": 67}  # 2 de 3 pedidos con «a»
    assert all(r["item_id"] != c3 for r in recs)  # 1 sola vez: no llega al mínimo
    assert any(r["reason"] == "bebida" for r in recs)  # el resto se completa con bebidas
    cart = client.get(f"/api/wrap-roll/upsell?items={a},{b}&context=cart").json()["items"]
    assert cart[0]["reason"] == "bebida"  # al cerrar el carrito sin bebida: bebidas primero
    d = client.get("/api/admin/dashboard", headers=ADMIN).json()
    assert d["pairs"][0]["orders"] == 2


def test_tiktok_videos_in_portal_and_store(client, monkeypatch):  # noqa: F811
    async def fake_resolve(url, slug):
        if "tiktok.com" not in url:
            raise tiktok.TikTokError("Pega un enlace de un video de tiktok.com")
        return {"id": "7301234567890123456", "url": url, "title": "Wrap <b>nuevo</b>", "author": "wraproll",
                "poster": "/uploads/x.jpg"}
    monkeypatch.setattr(tiktok, "resolve", fake_resolve)
    url = "https://www.tiktok.com/@wraproll/video/7301234567890123456"
    assert client.post("/api/wrap-roll/portal/tiktok", json={"url": url}).status_code == 401
    item = next(iter(menu_mod.load(1)["items"]))
    r = client.post("/api/wrap-roll/portal/tiktok", headers=ADMIN, json={"url": url, "item_id": item})
    assert r.status_code == 200, r.text
    v = r.json()[0]
    assert v["item_id"] == item and v["title"] == "Wrap bnuevo/b" and v["poster"] == "/uploads/x.jpg"
    bad = client.post("/api/wrap-roll/portal/tiktok", headers=ADMIN, json={"url": "https://evil.com/video/1"})
    assert bad.status_code == 422
    r = client.put("/api/wrap-roll/portal/tiktok", headers=ADMIN, json={"videos": [{"id": v["id"], "item_id": "no-existe"}]})
    assert r.json()[0]["item_id"] is None
    page = client.get("/wrap-roll").text
    assert "7301234567890123456" in page and "ttSheet" in page
    assert client.put("/api/wrap-roll/portal/tiktok", headers=ADMIN, json={"videos": []}).json() == []


@pytest.mark.parametrize("url", ["http://www.tiktok.com/@a/video/123456789", "https://tiktok.com.evil.com/video/1",
                                 "javascript:alert(1)", ""])
def test_tiktok_rejects_bad_links(url):
    import asyncio
    with pytest.raises(tiktok.TikTokError):
        asyncio.run(tiktok.resolve(url, "x"))
