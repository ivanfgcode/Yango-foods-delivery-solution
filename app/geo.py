"""Validación de la dirección del cliente.

Manda el pin (lat/lon), no el texto: en Bolivia la geocodificación por texto es poco fiable. El texto lo da el
geocodificador de Yango (`/eats/v1/persuggest/v1/finalsuggest`, acción pin_drop), el mismo que usa la web al
pedir, y el cliente lo completa con número, referencia y comentario para el mensajero.
"""
import math
import re
from typing import Any, Dict, List, Optional

from app.config import settings


class AddressError(ValueError):
    pass


def in_bounds(lat: float, lon: float) -> bool:
    la0, la1, lo0, lo1 = settings.bounds
    return la0 <= lat <= la1 and lo0 <= lon <= lo1


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_finalsuggest(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    results: List[Dict[str, Any]] = raw.get("results") or []
    if not results:
        return None
    r = results[0]
    pos = r.get("position") or [None, None]
    return {
        "text": r.get("text") or "",
        "short_text": r.get("short_text") or (r.get("title") or {}).get("text") or "",
        "street": r.get("street"),
        "house": r.get("house"),
        "city": (r.get("districts") or [None])[0] or r.get("city"),
        "country_code": r.get("country_code"),
        "uri": r.get("uri"),
        "lon": pos[0],
        "lat": pos[1],
    }


def parse_suggest(raw: Dict[str, Any], lat: float, lon: float, max_km: float = 40.0,
                  limit: int = 6) -> List[Dict[str, Any]]:
    """Búsqueda por texto → direcciones cercanas a la sucursal (Yango mezcla otras ciudades y países)."""
    out = []
    for r in raw.get("results") or []:
        pos = r.get("position") or []
        if len(pos) != 2:
            continue
        rlon, rlat = float(pos[0]), float(pos[1])
        if not in_bounds(rlat, rlon) or haversine_km(lat, lon, rlat, rlon) > max_km:
            continue
        out.append({"title": (r.get("title") or {}).get("text") or "", "subtitle": (r.get("subtitle") or {}).get("text") or "",
                    "lat": rlat, "lon": rlon, "exact": r.get("action") == "search"})
        if len(out) >= limit:
            break
    return out


def validate_address(a: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza y valida lo que manda el checkout. Lanza AddressError con un mensaje para el cliente."""
    try:
        lat, lon = float(a.get("lat")), float(a.get("lon"))
    except (TypeError, ValueError):
        raise AddressError("Marca tu ubicación en el mapa")
    if not (math.isfinite(lat) and math.isfinite(lon)) or not in_bounds(lat, lon):
        raise AddressError("La ubicación está fuera de la zona de servicio")
    text = re.sub(r"\s+", " ", str(a.get("text") or "")).strip()
    if len(text) < 3:
        raise AddressError("Escribe la dirección (calle o zona)")
    reference = re.sub(r"\s+", " ", str(a.get("reference") or "")).strip()
    if len(reference) < 3:
        raise AddressError("Agrega número de casa, edificio o una referencia")
    return {
        "lat": round(lat, 6), "lon": round(lon, 6),
        "text": text[:200],
        "reference": reference[:160],
        "comment": re.sub(r"\s+", " ", str(a.get("comment") or "")).strip()[:200],
        "geocoded": a.get("geocoded") if isinstance(a.get("geocoded"), dict) else None,
    }


_PHONE_DIGITS = re.compile(r"\D+")


def normalize_phone(phone: str) -> str:
    """Celular boliviano: 8 dígitos que empiezan por 6 o 7 (se acepta con o sin +591)."""
    digits = _PHONE_DIGITS.sub("", phone or "")
    prefix = settings.phone_prefix.lstrip("+")
    if digits.startswith(prefix) and len(digits) == len(prefix) + 8:
        digits = digits[len(prefix):]
    if len(digits) != 8 or digits[0] not in "67":
        raise AddressError("Escribe un celular válido (8 dígitos, empieza con 6 o 7)")
    return f"{settings.phone_prefix} {digits}"
