"""Modelo comercial: envío por tramos de distancia (mín–máx) y comisión sobre el subtotal.

Envío (por defecto, configurable con DELIVERY_TIERS):
    hasta 1 km  → Bs5–10
    1 a 3 km    → Bs10–15
    más de 3 km → Bs15–25
Dentro del tramo el precio sube con la distancia y con la demanda (recargo de Yango): solo llega al máximo lejos
y con alta demanda. La distancia y la demanda salen de la cotización de Yango; el cálculo se hace en el servidor.

Comisión: el restaurante paga a Yango un % del subtotal de cada pedido (COMMISSION_RATE, 3 %). Sin fijos.
"""
import math
from typing import Any, Dict, List, Optional

from app.config import settings


class TierError(ValueError):
    pass


def parse_tiers(spec: str) -> List[Dict[str, float]]:
    """'1:5-10,3:10-15,inf:15-25' → [{'up_to': 1, 'min': 5, 'max': 10}, ...] (ordenado, último con up_to=inf)."""
    tiers = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            km, rng = part.split(":")
            lo, hi = rng.split("-")
            up_to = math.inf if km.strip().lower() in ("inf", "+", "max") else float(km)
            tiers.append({"up_to": up_to, "min": float(lo), "max": float(hi)})
        except ValueError:
            raise TierError(f"Tramo de envío inválido: {part!r} (formato km:min-max)")
    if not tiers:
        raise TierError("No hay tramos de envío")
    tiers.sort(key=lambda t: t["up_to"])
    if not math.isinf(tiers[-1]["up_to"]):
        tiers.append({"up_to": math.inf, "min": tiers[-1]["max"], "max": tiers[-1]["max"]})
    for t in tiers:
        if t["min"] < 0 or t["max"] < t["min"]:
            raise TierError("Cada tramo necesita 0 ≤ mín ≤ máx")
    return tiers


def tiers() -> List[Dict[str, float]]:
    return parse_tiers(settings.delivery_tiers)


def _fmt_km(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def public_tiers() -> List[Dict[str, Any]]:
    """Tabla para mostrar al cliente: etiqueta de distancia + rango de precio."""
    out, prev = [], 0.0
    for t in tiers():
        if math.isinf(t["up_to"]):
            label = f"Más de {_fmt_km(prev)} km"
        elif prev == 0:
            label = f"Hasta {_fmt_km(t['up_to'])} km"
        else:
            label = f"{_fmt_km(prev)} a {_fmt_km(t['up_to'])} km"
        out.append({"label": label, "from_km": prev, "to_km": None if math.isinf(t["up_to"]) else t["up_to"],
                    "min": t["min"], "max": t["max"]})
        prev = t["up_to"]
    return out


def demand_level(quote: Dict[str, Any]) -> float:
    """0 (normal) … 1 (alta demanda). Usa el multiplicador del recargo si Yango lo manda."""
    if not quote.get("surge"):
        return 0.0
    info = quote.get("surge_info")
    if isinstance(info, dict):
        for k in ("multiplier", "coefficient", "value"):
            try:
                m = float(info.get(k))
            except (TypeError, ValueError):
                continue
            if m > 1:
                return max(0.0, min(1.0, m - 1))  # ×1,5 → 0,5 · ×2 o más → 1
    return 1.0


def delivery_fee(distance_km: float, demand: float = 0.0) -> Dict[str, Any]:
    """Precio de envío del modelo para una distancia y demanda (0–1). Redondeado a Bs entero."""
    d = max(0.0, float(distance_km or 0))
    prev = 0.0
    for i, t in enumerate(tiers()):
        if d <= t["up_to"]:
            hi = t["up_to"] if not math.isinf(t["up_to"]) else max(settings.delivery_max_km, prev + 1)
            pos = 0.0 if hi <= prev else max(0.0, min(1.0, (d - prev) / (hi - prev)))
            w = max(0.0, min(1.0, settings.demand_weight))
            f = pos * (1 - w) + max(0.0, min(1.0, demand)) * w
            fee = round(t["min"] + (t["max"] - t["min"]) * f)
            return {"fee": float(max(t["min"], min(t["max"], fee))), "min": t["min"], "max": t["max"],
                    "tier": i, "label": public_tiers()[i]["label"], "demand": round(demand, 2)}
        prev = t["up_to"]
    raise AssertionError("el último tramo es infinito")  # pragma: no cover


def commission_rate(partner: Optional[Dict[str, Any]] = None) -> float:
    rate = (partner or {}).get("commission_rate")
    return float(rate) if rate is not None else settings.commission_rate


def commission(subtotal: float, partner: Optional[Dict[str, Any]] = None) -> float:
    return round(max(0.0, subtotal) * commission_rate(partner), 2)
