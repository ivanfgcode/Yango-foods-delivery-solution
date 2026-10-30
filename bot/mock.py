"""Yango simulado a partir de respuestas reales guardadas (tests/fixtures). Para desarrollo y pruebas."""
import asyncio
import copy
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import ROOT
from app.geo import haversine_km
from bot.base import PlacementMismatch, YangoSource

FIX = ROOT / "tests" / "fixtures"
SOUTH = "mock__sur"
SOUTH_ID_OFFSET = 1000


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
        self._text_suggest = _load("suggest_text.json")
        self.placed: List[Dict[str, Any]] = []
        self.closed = False
        self.zone_closed_beyond_km: Optional[float] = None
        self.fail_next = 0
        self.mismatch_next = False
        # Seguimiento simulado (vendor app): estado forzado por pedido o, si no, avance con el tiempo.
        self.vendor_status: Dict[str, str] = {}
        self.vendor_timeline = [(0, "new"), (20, "accepted"), (60, "ready"), (120, "in_delivery"), (300, "released")]
        self._placed_at: Dict[str, float] = {}
        place = self._catalog["payload"]["foundPlace"]["place"]
        self.lat = place["address"]["location"]["latitude"]
        self.lon = place["address"]["location"]["longitude"]
        self._brand_place = _load("brand_place_centro.json")
        # Dos sucursales simuladas: la del fixture y otra ~5 km al sur con otros ids y precios (+1 Bs).
        self.branches = {place["slug"]: (self.lat, self.lon, place["name"]),
                         SOUTH: (self.lat - 0.045, self.lon, "Wrap & Roll - Sur (simulada)")}

    def _branch(self, slug: str) -> Tuple[float, float, str]:
        return self.branches.get(slug) or next(iter(self.branches.values()))

    async def brand_place(self, account: str, brand_slug: str, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        await asyncio.sleep(self.latency)
        slug, (blat, blon, name) = min(self.branches.items(), key=lambda kv: haversine_km(lat, lon, kv[1][0], kv[1][1]))
        km = haversine_km(lat, lon, blat, blon)
        if km > self.radius_km:
            return None
        raw = copy.deepcopy(self._brand_place)
        fp = raw["payload"]["foundPlace"]
        fp["place"].update({"slug": slug, "name": name})
        fp["place"].setdefault("address", {})["location"] = {"latitude": blat, "longitude": blon}
        lp = fp.setdefault("locationParams", {})
        lp.update({"available": not self.closed, "availableByLocation": True, "availableByTime": not self.closed,
                   "distance": km, "deliveryTime": {"min": int(10 + km * 3), "max": int(20 + km * 3)}})
        return raw

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
        blat, blon, name = self._branch(place_slug)
        km = haversine_km(lat, lon, blat, blon)
        ok = km <= self.radius_km
        fp["place"].update({"slug": place_slug if place_slug in self.branches else fp["place"]["slug"], "name": name})
        lp = fp["locationParams"]
        # Local cerrado (en todas partes) o zona cerrada por demanda (lejos del local, pero dentro del radio).
        by_time = not self.closed and not (self.zone_closed_beyond_km is not None and km > self.zone_closed_beyond_km)
        lp.update({"available": ok and by_time, "availableByLocation": ok, "availableByTime": by_time, "distance": km,
                   "deliveryTime": {"min": int(10 + km * 3), "max": int(20 + km * 3)} if ok else {"min": 0, "max": 0}})
        fp["place"]["deliveryThresholds"] = self._fee_tiers(km) if ok else []
        return raw

    async def menu(self, account: str, place_slug: str, lat: float, lon: float) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        raw = copy.deepcopy(self._menu)
        if place_slug == SOUTH:  # otra sucursal: mismos platos, otros ids y +1 Bs
            def walk(cats: List[Dict[str, Any]]) -> None:
                for c in cats or []:
                    for it in c.get("items") or []:
                        it["id"] = int(it["id"]) + SOUTH_ID_OFFSET
                        for k in ("price", "decimalPrice"):
                            if it.get(k) is not None:
                                it[k] = float(it[k]) + 1
                        for k in ("promoPrice", "decimalPromoPrice"):
                            if it.get(k) is not None:
                                it[k] = float(it[k]) + 1
                    walk(c.get("categories") or [])
            walk(raw.get("payload", raw).get("categories"))
        return raw

    async def reverse_geocode(self, account: str, lat: float, lon: float) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        raw = copy.deepcopy(self._suggest)
        r = raw["results"][0]
        r["position"] = [lon, lat]
        r["text"] = f"Santa Cruz de la Sierra, Calle simulada {abs(int(lat * 1000)) % 90 + 1}"
        r["short_text"] = r["text"].split(", ", 1)[1]
        return raw

    async def search_address(self, account: str, text: str, lat: float, lon: float) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        return copy.deepcopy(self._text_suggest)

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
        self._placed_at[nr] = time.monotonic()
        self.placed.append({"account": account, "order": order["number"], "yango_order_nr": nr,
                            "comment": order["yango_comment"]})
        return {"yango_order_nr": nr, "yango_total": order["subtotal"] + order["delivery_fee"],
                "delivery_fee": order["delivery_fee"]}

    def _vendor_raw(self, nr: str) -> str:
        if nr in self.vendor_status:
            return self.vendor_status[nr]
        age = time.monotonic() - self._placed_at.get(nr, time.monotonic())
        return [st for t, st in self.vendor_timeline if age >= t][-1]

    async def vendor_active(self, account: str) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        groups: Dict[str, List[Dict[str, Any]]] = {s: [] for s in ("new", "accepted", "ready", "in_delivery")}
        for nr in self._placed_at:
            raw = self._vendor_raw(nr)
            if raw in groups:
                groups[raw].append({"order_nr": nr, "status": raw, "place_id": 1, "sum": "0"})
        return {"orders_by_status": [{"status": k, "orders": v} for k, v in groups.items()]}

    async def vendor_order(self, account: str, order_nr: str) -> Optional[Dict[str, Any]]:
        await asyncio.sleep(self.latency)
        return {"order_nr": order_nr, "status": self._vendor_raw(order_nr)} if order_nr in self._placed_at else None

    async def order_tracking(self, account: str) -> Dict[str, Any]:
        await asyncio.sleep(self.latency)
        return {"payload": {"trackedOrders": [{"order": {"orderNr": nr}, "status": {"id": self._vendor_raw(nr)}}
                                              for nr in self._placed_at]}}
