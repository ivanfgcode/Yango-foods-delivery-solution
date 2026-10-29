"""Yango simulado a partir de respuestas reales guardadas (tests/fixtures). Para desarrollo y pruebas."""
import asyncio
import copy
import json
import random
from pathlib import Path
from typing import Any, Dict, List

from app.config import ROOT
from app.geo import haversine_km
from bot.base import PlacementMismatch, YangoSource

FIX = ROOT / "tests" / "fixtures"


def _load(name: str) -> Dict[str, Any]:
    return json.loads((FIX / name).read_text())


class MockSource(YangoSource):
    name = "mock"

    def __init__(self, latency: float = 0.03, radius_km: float = 8.0) -> None:
        self.latency = latency
        self.radius_km = radius_km
        self._catalog = _load("catalog_available.json")
        self._menu = _load("menu_retrieve.json")
        self._suggest = _load("finalsuggest_pin_drop.json")
        self.placed: List[Dict[str, Any]] = []
        self.fail_next = 0
        self.mismatch_next = False
        place = self._catalog["payload"]["foundPlace"]["place"]
        self.lat = place["address"]["location"]["latitude"]
        self.lon = place["address"]["location"]["longitude"]

    async def resolve_place(self, account: str, yango_url: str) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        p = self._catalog["payload"]["foundPlace"]["place"]
        return {"place_slug": p["slug"], "place_id": p["id"], "name": p["name"], "lat": self.lat, "lon": self.lon,
                "address": p["address"]["short"], "logo_url": None, "accent_color": "#EF3C35"}

    def _fee_tiers(self, km: float) -> List[Dict[str, Any]]:
        if km < 3:
            return [{"orderPrice": {"min": 15, "max": None}, "deliveryCost": 0, "name": "para pedidos desde Bs15"}]
        fee = 7 if km < 6 else 12
        return [
            {"orderPrice": {"min": 15, "max": 60}, "deliveryCost": fee, "name": "para pedidos de Bs15 a Bs60"},
            {"orderPrice": {"min": 60, "max": None}, "deliveryCost": max(0, fee - 5), "name": "desde Bs60"},
        ]

    async def catalog(self, account: str, place_slug: str, lat: float, lon: float) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        raw = copy.deepcopy(self._catalog)
        fp = raw["payload"]["foundPlace"]
        km = haversine_km(lat, lon, self.lat, self.lon)
        ok = km <= self.radius_km
        lp = fp["locationParams"]
        lp.update({"available": ok, "availableByLocation": ok, "distance": km,
                   "deliveryTime": {"min": int(10 + km * 3), "max": int(20 + km * 3)} if ok else {"min": 0, "max": 0}})
        fp["place"]["deliveryThresholds"] = self._fee_tiers(km) if ok else []
        return raw

    async def menu(self, account: str, place_slug: str, lat: float, lon: float) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        return copy.deepcopy(self._menu)

    async def reverse_geocode(self, account: str, lat: float, lon: float) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        raw = copy.deepcopy(self._suggest)
        r = raw["results"][0]
        r["position"] = [lon, lat]
        r["text"] = f"Santa Cruz de la Sierra, Calle simulada {abs(int(lat * 1000)) % 90 + 1}"
        r["short_text"] = r["text"].split(", ", 1)[1]
        return raw

    async def check_session(self, account: str) -> Dict[str, Any]:
        return {"ok": True, "detail": "simulada"}

    async def place_order(self, account: str, partner: Dict[str, Any], order: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("fallo simulado al colocar")
        if self.mismatch_next:
            self.mismatch_next = False
            raise PlacementMismatch("envío simulado distinto")
        nr = f"261029-BO{random.randint(1000000, 9999999)}"
        self.placed.append({"account": account, "order": order["number"], "yango_order_nr": nr,
                            "comment": order["yango_comment"]})
        return {"yango_order_nr": nr, "yango_total": order["subtotal"] + order["delivery_fee"],
                "delivery_fee": order["delivery_fee"]}
