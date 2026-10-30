"""Parsers con respuestas reales de food.yango.com (2026-10-29, Wrap & Roll Paragua)."""
import json
from pathlib import Path

import pytest

from app import geo, menu, quotes
from bot.yango_web import fill_template, item_options_payload

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_menu_real():
    m = menu.parse_menu_retrieve(load("menu_retrieve.json"))
    assert len(m["categories"]) == 13
    assert len(m["items"]) == 60  # algunos platos aparecen en varias categorías
    combo = m["items"]["3009756314"]
    assert combo["name"] == "Wrap M + gaseosa"
    assert combo["price"] == 45 and combo["promo_price"] == 43
    g = combo["groups"][0]
    assert g["required"] and g["min"] == 1 and g["max"] == 1
    assert combo["image"].startswith("https://food.yango.com/images/")


def test_price_line_validates_groups():
    m = menu.parse_menu_retrieve(load("menu_retrieve.json"))
    item = m["items"]["3009756314"]
    with pytest.raises(menu.MenuError):
        menu.price_line(m, item["id"], 1, {})
    sel = {g["id"]: [g["options"][0]["id"]] for g in item["groups"] if g["required"] or g["min"]}
    line, amount = menu.price_line(m, item["id"], 2, sel)
    extra = sum(o["price"] for o in line["options"])
    assert amount == (43 + extra) * 2
    with pytest.raises(menu.MenuError):
        menu.price_line(m, item["id"], 1, {**sel, "999": ["1"]})


def test_catalog_available():
    q = quotes.parse_catalog(load("catalog_available.json"))
    assert q["available"] and q["reason"] is None
    assert q["place_slug"] == "wrap__roll__paragua"
    assert q["thresholds"] == [{"min": 15.0, "max": None, "fee": 0.0, "label": "para pedidos desde Bs15"}]
    assert q["min_order"] == 15 and q["service_fee"] == 3
    assert (q["eta_min"], q["eta_max"]) == (15, 25)
    s = quotes.price_for(q, 45)
    assert s["delivery_fee"] == 0 and s["total"] == 48 and s["can_order"]
    assert quotes.price_for(q, 10)["below_min_order"]


def test_catalog_out_of_zone():
    q = quotes.parse_catalog(load("catalog_out_of_zone.json"))
    assert not q["available"] and q["reason"] == "fuera_de_zona"
    assert not quotes.price_for(q, 50)["can_order"]


def test_fee_tiers():
    q = {"available": True, "thresholds": [{"min": 15, "max": 37, "fee": 7}, {"min": 37, "max": None, "fee": 0}],
         "service_fee": 3, "min_order": 15}
    assert quotes.fee_for(q, 20) == 7
    assert quotes.fee_for(q, 37) == 0
    assert quotes.fee_for(q, 10) is None


def test_finalsuggest():
    a = geo.parse_finalsuggest(load("finalsuggest_pin_drop.json"))
    assert a["short_text"] == "Plaza Principal 24 de Septiembre"
    assert (a["lat"], a["lon"]) == (-17.7833, -63.1821)


def test_brand_place_fixture_has_place_slug():
    p = load("brand_place.json")["payload"]["foundPlace"]["place"]
    assert p["slug"] == "wrap__roll__paragua" and p["brand"]["slug"] == "wrap_roll_-_san_martin"


def test_address_and_phone():
    ok = geo.validate_address({"lat": -17.78, "lon": -63.18, "text": "Av. Busch", "reference": "Casa 4"})
    assert ok["lat"] == -17.78
    with pytest.raises(geo.AddressError):
        geo.validate_address({"lat": 40.4, "lon": -3.7, "text": "Madrid", "reference": "x x x"})
    with pytest.raises(geo.AddressError):
        geo.validate_address({"lat": -17.78, "lon": -63.18, "text": "Av. Busch", "reference": ""})
    assert geo.normalize_phone("742 75 986") == "+591 74275986"
    assert geo.normalize_phone("+591 74275986") == "+591 74275986"
    with pytest.raises(geo.AddressError):
        geo.normalize_phone("2123456")


def test_cart_payload_templates():
    line = {"options": [{"group_id": "10", "option_id": "1"}, {"group_id": "10", "option_id": "2"},
                        {"group_id": "11", "option_id": "3"}]}
    opts = item_options_payload(line)
    assert opts == [{"group_id": 10, "group_options": [1, 2]}, {"group_id": 11, "group_options": [3]}]
    body = fill_template({"item_id": "{item_id}", "q": "{quantity}", "opts": "{item_options}", "s": "x-{item_id}"},
                         {"item_id": 5, "quantity": 2, "item_options": opts})
    assert body == {"item_id": 5, "q": 2, "opts": opts, "s": "x-5"}


def test_catalog_zone_closed_real():
    """Respuesta real (2026-10-30, centro de Santa Cruz): la zona no se atiende ahora y Yango sugiere otra sucursal."""
    q = quotes.parse_catalog(json.loads((FIX / "catalog_zone_closed.json").read_text()))
    assert q["available"] is False and q["reason"] == "cerrado"
    assert q["other_branch"] == "wrap__roll__megacenter"


def test_parse_suggest_keeps_only_nearby():
    raw = json.loads((FIX / "suggest_text.json").read_text())
    res = geo.parse_suggest(raw, -17.7648, -63.1474)
    assert res[0]["title"] == "Rafael Peña, 100" and res[0]["exact"]
    assert all("México" not in r["subtitle"] and "Cochabamba" not in r["subtitle"] for r in res)
    assert abs(res[0]["lat"] + 17.7777) < 0.001
