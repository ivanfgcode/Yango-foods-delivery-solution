"""Parsers con respuestas reales de food.yango.com (2026-10-29, Wrap & Roll Paragua)."""
import json
from pathlib import Path

import pytest

from app import geo, menu, pricing, quotes
from app.config import settings
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
    # Modelo por distancia (por defecto): el envío sale del tramo, sin tarifa de servicio fija.
    band = pricing.delivery_fee(q["distance_km"])
    assert s["delivery_fee"] == band["fee"] and s["service_fee"] == 0 and s["total"] == 45 + band["fee"]
    assert s["yango_fee"] == 0 and s["can_order"]  # lo que cobra Yango a la cuenta, para el control del bot
    assert band["min"] <= s["delivery_fee"] <= band["max"]
    assert quotes.price_for(q, 10)["below_min_order"]


def test_catalog_available_yango_mode(monkeypatch):
    monkeypatch.setattr(settings, "delivery_fee_mode", "yango")
    monkeypatch.setattr(settings, "charge_service_fee", True)
    q = quotes.parse_catalog(load("catalog_available.json"))
    s = quotes.price_for(q, 45)
    assert s["delivery_fee"] == 0 and s["total"] == 48 and s["can_order"]


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


def test_delivery_tiers_model():
    """<1 km Bs5–10 · hasta 3 km Bs10–15 · más de 3 km Bs15–25, según distancia y demanda."""
    t = pricing.public_tiers()
    assert [(x["label"], x["min"], x["max"]) for x in t] == [
        ("Hasta 1 km", 5, 10), ("1 a 3 km", 10, 15), ("Más de 3 km", 15, 25)]
    assert pricing.delivery_fee(0)["fee"] == 5                     # al lado, sin demanda: el mínimo
    assert pricing.delivery_fee(0.99)["max"] == 10
    assert pricing.delivery_fee(1.0)["tier"] == 0 and pricing.delivery_fee(1.01)["tier"] == 1
    assert pricing.delivery_fee(3.2)["min"] == 15
    near, near_peak = pricing.delivery_fee(2.0), pricing.delivery_fee(2.0, 1.0)
    assert 10 <= near["fee"] < near_peak["fee"] <= 15              # la demanda sube el precio
    assert pricing.delivery_fee(30, 1.0)["fee"] == 25              # lejos y alta demanda: el máximo
    assert pricing.delivery_fee(30, 0)["fee"] < 25                 # sin demanda no llega al máximo
    for km in (0, 0.5, 1, 1.5, 3, 4, 6, 8, 12):
        for d in (0, 0.5, 1):
            f = pricing.delivery_fee(km, d)
            assert f["min"] <= f["fee"] <= f["max"]


def test_demand_from_surge():
    assert pricing.demand_level({"surge": False}) == 0
    assert pricing.demand_level({"surge": True, "surge_info": {"title": "alta demanda"}}) == 1
    assert pricing.demand_level({"surge": True, "surge_info": {"multiplier": 1.5}}) == 0.5


def test_parse_tiers_custom():
    t = pricing.parse_tiers("2:6-9, 5:9-14")
    assert t[-1]["up_to"] == float("inf") and t[-1]["min"] == 14  # sin último tramo infinito: se cierra al máximo
    with pytest.raises(pricing.TierError):
        pricing.parse_tiers("1:10-5")


def test_commission_three_percent():
    assert settings.commission_rate == 0.03
    assert pricing.commission(100) == 3.0 and pricing.commission(43) == 1.29
    assert pricing.commission(100, {"commission_rate": 0.05}) == 5.0


def test_pdf_style_validation():
    from app import themes
    ok = themes.validate_pdf({"primary": "#112233", "columns": 9, "template": "pizarra", "title": "<b>Menú</b>",
                              "hidden_categories": ["cabc123"]})
    assert ok == {"primary": "#112233", "columns": 3, "template": "pizarra", "title": "bMenú/b",
                  "hidden_categories": ["cabc123"]}
    for bad in ({"primary": "red;}"}, {"heading_font": "Comic"}, {"template": "x"},
                {"hidden_categories": ["a;b"]}, {"page_size": "A0"}):
        with pytest.raises(ValueError):
            themes.validate_pdf(bad)
