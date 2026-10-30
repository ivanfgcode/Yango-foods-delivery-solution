"""Ubicación primero: la sucursal que Yango asigna a la dirección decide menú, precios, envío y dónde se pide."""
import pytest

from app import branches, menu as menu_mod, upsell
from app.config import settings
from bot.mock import SOUTH, SOUTH_ID_OFFSET
from tests.test_flow import ADMIN, NEAR, client  # noqa: F401  (fixture)


@pytest.fixture()
def south(client):  # noqa: F811
    from app import runtime
    src = runtime.get_runtime().source
    branches.clear_cache()
    lat, lon, _ = src.branches[SOUTH]
    return {"lat": lat - 0.002, "lon": lon}


def body_for(c, point, branch, item_id, **over):
    m = c.get(f"/api/wrap-roll/menu?branch={branch}").json()
    it = m["items"][item_id]
    opts = {g["id"]: [g["options"][0]["id"]] for g in it["groups"] if g["required"] or g["min"]}
    body = {"customer": {"first_name": "Ana", "last_name": "Rojas", "phone": "74275986"},
            "address": {**point, "text": "Av. Santos Dumont", "reference": "Casa 3"}, "payment": {"method": "cash"},
            "items": [{"item_id": item_id, "quantity": 1, "options": opts}], "branch": branch}
    body.update(over)
    return body


def test_branch_for_point(client, south):  # noqa: F811
    near = client.get("/api/wrap-roll/branch", params=NEAR).json()["branch"]
    assert near["primary"] and near["slug"] != SOUTH
    b = client.get("/api/wrap-roll/branch", params=south).json()["branch"]
    assert b["slug"] == SOUTH and b["name"].startswith("Wrap & Roll - Sur") and not b["primary"]
    far = client.get("/api/wrap-roll/branch", params={"lat": -17.2, "lon": -63.1}).json()
    assert far == {"branch": None}  # ninguna sucursal llega


def test_branch_menu_prices_and_alias(client, south):  # noqa: F811
    client.get("/api/wrap-roll/branch", params=south)  # Yango la asigna y queda conocida
    main = client.get("/api/wrap-roll/menu").json()
    m = client.get(f"/api/wrap-roll/menu?branch={SOUTH}").json()
    pid = "3009756314"
    sid = str(int(pid) + SOUTH_ID_OFFSET)
    assert m["branch"]["slug"] == SOUTH and m["items"][sid]["price"] == main["items"][pid]["price"] + 1
    assert m["alias"][pid] == sid  # lo configurado con ids de la principal se traduce por nombre
    assert client.get("/api/wrap-roll/menu?branch=otra_marca").status_code == 404


def test_order_in_assigned_branch(client, south, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "order_placement_enabled", False)
    client.get("/api/wrap-roll/branch", params=south)
    sid = str(3009756314 + SOUTH_ID_OFFSET)
    q = client.post("/api/wrap-roll/quote", json={**south, "subtotal": 46, "branch": SOUTH}).json()
    assert q["available"] and q["branch"] == SOUTH and q["place_slug"] == SOUTH
    r = client.post("/api/wrap-roll/orders", json=body_for(client, south, SOUTH, sid))
    assert r.status_code == 200, r.text
    o = client.get("/api/admin/orders", headers=ADMIN).json()[0]
    assert o["quote"]["place_slug"] == SOUTH and o["subtotal"] == 44  # promo Bs43 en la principal, +1 en esta sucursal
    # el navegador dice otra sucursal que la que atiende la dirección: no se crea
    bad = client.post("/api/wrap-roll/orders", json=body_for(client, south, SOUTH, sid, address={**NEAR, "text": "Av. Busch", "reference": "Casa 3"}))
    assert bad.status_code == 422 and "la atiende" in bad.json()["detail"]


def test_upsell_counts_by_name_across_branches(client, south, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "order_placement_enabled", False)
    upsell.clear_cache()
    client.get("/api/wrap-roll/branch", params=south)
    m_main = menu_mod.load(1)
    # mismo par de productos pedido en la sucursal sur (otros ids): cuenta para las dos
    simple = [i for i, it in m_main["items"].items() if it["available"] and not any(g["required"] or g["min"] for g in it["groups"])
              and i not in set(upsell.drink_ids(m_main))][:2]
    a, b = simple
    sa, sb = str(int(a) + SOUTH_ID_OFFSET), str(int(b) + SOUTH_ID_OFFSET)
    for _ in range(2):
        body = body_for(client, south, SOUTH, sa)
        body["items"].append({"item_id": sb, "quantity": 1, "options": {}})
        assert client.post("/api/wrap-roll/orders", json=body).status_code == 200
    recs = client.get(f"/api/wrap-roll/upsell?items={a}").json()["items"]
    assert recs[0]["item_id"] == b and recs[0]["reason"] == "combinado"
    recs = client.get(f"/api/wrap-roll/upsell?items={sa}&branch={SOUTH}").json()["items"]
    assert recs[0]["item_id"] == sb
