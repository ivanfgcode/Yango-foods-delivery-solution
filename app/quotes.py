"""Cotización de envío con la respuesta de `/api/v2/catalog/{place_slug}?latitude&longitude&shippingType=delivery`.

No toca el carrito ni la dirección de la cuenta: es la misma consulta que hace la web al mover el pin, así que
se pueden hacer muchas a la vez. Caché por punto (~11 m) para absorber el pico (pin que se mueve, recargas).
"""
import asyncio
import re
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.config import settings

_NUM = re.compile(r"(\d+(?:[.,]\d+)?)")


def _money(text: Any) -> Optional[float]:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUM.search(str(text))
    return float(m.group(1).replace(",", ".")) if m else None


def parse_catalog(raw: Dict[str, Any]) -> Dict[str, Any]:
    """JSON de Yango → cotización normalizada (sin depender del subtotal)."""
    fp = (raw.get("payload") or {}).get("foundPlace") or {}
    place = fp.get("place") or {}
    lp = fp.get("locationParams") or {}
    thresholds: List[Dict[str, Any]] = []
    for t in place.get("deliveryThresholds") or []:
        op = t.get("orderPrice") or {}
        thresholds.append({
            "min": _money(op.get("decimalMin", op.get("min"))) or 0.0,
            "max": _money(op.get("decimalMax", op.get("max"))),
            "fee": _money(t.get("decimalDeliveryCost", t.get("deliveryCost"))) or 0.0,
            "label": (t.get("name") or "").strip(),
        })
    by_location = bool(lp.get("availableByLocation"))
    by_time = bool(lp.get("availableByTime"))
    available = bool(lp.get("available")) and by_location and by_time
    if available:
        reason = None
    elif not by_location:
        reason = "fuera_de_zona"
    elif not by_time:
        reason = "cerrado"
    else:
        reason = "no_disponible"
    constraints = place.get("constraints") or {}
    min_order = _money((constraints.get("minimum_order_price") or {}).get("value"))
    if min_order is None:
        min_order = _money(place.get("minimalOrderPrice")) or 0.0
    surge = fp.get("surge")
    eta = lp.get("deliveryTime") or {}
    return {
        "available": available,
        "reason": reason,
        "place_id": place.get("id"),
        "place_slug": place.get("slug"),
        "distance_km": round(float(lp.get("distance") or 0), 2),
        "eta_min": eta.get("min"),
        "eta_max": eta.get("max"),
        "available_from": lp.get("availableFrom"),
        "available_to": lp.get("availableTo"),
        "other_branch": lp.get("availableSlug"),  # otra sucursal de la marca que sí atiende ese punto
        "thresholds": thresholds,
        "fallback_fee": _money(((lp.get("shippingInfoAction") or {}).get("deliveryFee") or {}).get("name")),
        "service_fee": _money((place.get("service_fee") or {}).get("amount")) or 0.0,
        "min_order": min_order,
        "surge": bool(surge),
        "surge_info": surge,
        "conditions": place.get("deliveryConditions"),
        "currency": ((place.get("currency") or {}).get("sign")) or settings.currency,
    }


def fee_for(quote: Dict[str, Any], subtotal: float) -> Optional[float]:
    """Costo de envío para un subtotal. None si el subtotal no llega al mínimo de ningún tramo."""
    ths = quote.get("thresholds") or []
    if not ths:
        return quote.get("fallback_fee")
    for t in sorted(ths, key=lambda t: t["min"], reverse=True):
        if subtotal >= t["min"] and (t["max"] is None or subtotal < t["max"]):
            return t["fee"]
    return None


def price_for(quote: Dict[str, Any], subtotal: float) -> Dict[str, Any]:
    """Resumen para el cliente: envío, tarifa de servicio, total y si se puede pedir."""
    out = dict(quote)
    fee = fee_for(quote, subtotal) if quote.get("available") else None
    out["subtotal"] = round(subtotal, 2)
    out["delivery_fee"] = fee
    below_min = subtotal < (quote.get("min_order") or 0)
    out["below_min_order"] = below_min
    out["can_order"] = bool(quote.get("available")) and fee is not None and not below_min and subtotal > 0
    out["total"] = round(subtotal + (fee or 0) + (quote.get("service_fee") or 0), 2) if fee is not None else None
    return out


class QuoteService:
    """Caché + deduplicación de consultas iguales en vuelo."""

    def __init__(self, fetch: Callable[[str, str, float, float], Awaitable[Dict[str, Any]]]) -> None:
        self._fetch = fetch  # (account, place_slug, lat, lon) -> JSON crudo de Yango
        self._cache: Dict[Tuple, Tuple[float, Dict[str, Any]]] = {}
        self._inflight: Dict[Tuple, asyncio.Future] = {}
        self.stats = {"hits": 0, "misses": 0, "errors": 0}

    def _key(self, place_slug: str, lat: float, lon: float) -> Tuple:
        p = settings.quote_precision
        return (place_slug, round(lat, p), round(lon, p))

    async def quote(self, account: str, place_slug: str, lat: float, lon: float,
                    fresh: bool = False) -> Dict[str, Any]:
        key = self._key(place_slug, lat, lon)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and not fresh and now - hit[0] < settings.quote_cache_seconds:
            self.stats["hits"] += 1
            return hit[1]
        if key in self._inflight:
            return await asyncio.shield(self._inflight[key])
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        self.stats["misses"] += 1
        try:
            raw = await self._fetch(account, place_slug, key[1], key[2])
            q = parse_catalog(raw)
            q["quoted_at"] = time.time()
            self._cache[key] = (time.monotonic(), q)
            if len(self._cache) > 20000:
                for k in list(self._cache)[:5000]:
                    self._cache.pop(k, None)
            fut.set_result(q)
            return q
        except Exception as e:
            self.stats["errors"] += 1
            fut.set_exception(e)
            fut.exception()  # evita el aviso de excepción no recuperada si nadie más espera
            raise
        finally:
            self._inflight.pop(key, None)
