"""Sucursales: qué local de la marca atiende la dirección del cliente, como hace food.yango.com.

Con la ubicación, `eats-catalog/v2/brand/place?brand_slug&latitude&longitude` devuelve la sucursal asignada
(centro de Santa Cruz → Equipetrol, sur → Palmas...) o 404 si ninguna llega. La tienda pide la ubicación primero
y muestra el menú, los precios y el envío de esa sucursal. Cada sucursal tiene sus propios ids de producto en
Yango: lo que se configura una vez (más vendidos, TikTok, upselling) se traduce por nombre de producto.
"""
import json
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app import db, menu as menu_mod
from app.config import settings

_cache: Dict[Tuple, Tuple[float, Optional[Dict[str, Any]]]] = {}
FIND_CACHE_SECONDS = 120


def primary(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not p.get("place_slug"):
        return None
    return {"slug": p["place_slug"], "name": p.get("place_name") or p["name"], "lat": p.get("place_lat"),
            "lon": p.get("place_lon"), "address": p.get("place_address"), "primary": True}


def parse_brand_place(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    fp = (raw.get("payload") or {}).get("foundPlace") or {}
    place = fp.get("place") or {}
    if not place.get("slug"):
        return None
    loc = (place.get("address") or {}).get("location") or {}
    lp = fp.get("locationParams") or {}
    eta = lp.get("deliveryTime") or {}
    return {"slug": place["slug"], "place_id": place.get("id"), "name": place.get("name") or place["slug"],
            "lat": loc.get("latitude"), "lon": loc.get("longitude"), "address": (place.get("address") or {}).get("short"),
            "available": bool(lp.get("available")), "by_time": bool(lp.get("availableByTime")),
            "eta_min": eta.get("min") or None, "eta_max": eta.get("max") or None}


def get(p: Dict[str, Any], slug: Optional[str]) -> Optional[Dict[str, Any]]:
    """Sucursal conocida del partner (la principal o una que Yango ya asignó). None si no es suya."""
    main = primary(p)
    if not slug or (main and slug == main["slug"]):
        return main
    row = db.one("SELECT * FROM branches WHERE place_slug=? AND partner_id=?", (slug, p["id"]))
    if not row:
        return None
    return {"slug": row["place_slug"], "name": row["name"], "lat": row["lat"], "lon": row["lon"],
            "address": row["address"], "primary": False}


async def find(source: Any, p: Dict[str, Any], lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Sucursal que Yango asigna a ese punto, o None si ninguna llega. Caché corta por punto (~110 m)."""
    if not p.get("brand_slug"):
        return primary(p)
    key = (p["id"], round(lat, 3), round(lon, 3))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < FIND_CACHE_SECONDS:
        return hit[1]
    raw = await source.brand_place(p["account"], p["brand_slug"], lat, lon)
    b = parse_brand_place(raw) if raw else None
    if b:
        b["primary"] = b["slug"] == p.get("place_slug")
        if not b["primary"]:
            db.execute("""INSERT INTO branches(place_slug, partner_id, place_id, name, lat, lon, address, updated_at)
                          VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(place_slug) DO UPDATE SET name=excluded.name,
                          lat=excluded.lat, lon=excluded.lon, address=excluded.address, updated_at=excluded.updated_at""",
                       (b["slug"], p["id"], b["place_id"], b["name"], b["lat"], b["lon"], b["address"], db.now_iso()))
    _cache[key] = (time.monotonic(), b)
    if len(_cache) > 20000:
        _cache.clear()
    return b


async def menu(source: Any, p: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Menú de la sucursal: la principal se sincroniza en segundo plano; las demás al pedirlas (máx. cada
    MENU_SYNC_MINUTES)."""
    if b["primary"]:
        return menu_mod.load(p["id"])
    row = db.one("SELECT data, synced_at FROM branch_menus WHERE place_slug=?", (b["slug"],))
    if row:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["synced_at"])).total_seconds()
        if age < settings.menu_sync_minutes * 60:
            return {**json.loads(row["data"]), "synced_at": row["synced_at"]}
    try:
        raw = await source.menu(p["account"], b["slug"], b["lat"], b["lon"])
        parsed = menu_mod.parse_menu_retrieve(raw)
        if not parsed["items"]:
            raise RuntimeError("menú vacío")
    except Exception:
        if row:  # mejor el último conocido que nada
            return {**json.loads(row["data"]), "synced_at": row["synced_at"]}
        raise
    now = db.now_iso()
    db.execute("""INSERT INTO branch_menus(place_slug, data, synced_at) VALUES (?,?,?)
                  ON CONFLICT(place_slug) DO UPDATE SET data=excluded.data, synced_at=excluded.synced_at""",
               (b["slug"], json.dumps(parsed, ensure_ascii=False), now))
    return {**parsed, "synced_at": now}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def alias(src: Dict[str, Any], dst: Dict[str, Any]) -> Dict[str, str]:
    """ids de un menú → ids del mismo producto (por nombre) en el menú de otra sucursal."""
    by_name: Dict[str, str] = {}
    for iid, it in dst["items"].items():
        by_name.setdefault(_norm(it["name"]), iid)
    return {iid: by_name[_norm(it["name"])] for iid, it in src["items"].items() if _norm(it["name"]) in by_name}


def clear_cache() -> None:
    _cache.clear()
