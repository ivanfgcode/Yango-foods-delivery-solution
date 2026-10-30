"""Upselling: «Otras personas lo combinaron con» a partir de lo que se pide junto en la web de cada restaurante.

Para cada producto se cuentan los pedidos (no cancelados, últimos UPSELL_DAYS días) en los que aparece junto a
otro. La sugerencia ordena por confianza (veces juntos / veces que se pidió el producto) con un mínimo de
UPSELL_MIN_PAIRS pedidos. Mientras no hay datos suficientes, o para cerrar el carrito, se sugieren bebidas
(categorías o nombres de bebida del menú de Yango), las más vendidas primero. Solo sugiere: los precios los
recalcula el servidor al crear el pedido, como siempre.
"""
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.config import settings

DRINK_RE = re.compile(r"bebida|refresco|gaseosa|jugo|zumo|soda|agua|drink|\bt[eé]\b|cerveza|limonada|coca|fanta|"
                      r"sprite|pepsi|batido|smoothie|caf[eé]|frappe|malteada|mocochinchi|chicha|energ", re.I)
# Categorías: por tipo de bebida, nunca por marca («Combos Coca-Cola» no es una categoría de bebidas).
DRINK_CAT_RE = re.compile(r"bebida|refresco|gaseosas|jugos|zumos|drinks|smoothie|batido|\bt[eé]s?\b|caf[eé]|"
                          r"frozt|limonada|cerveza|licuado", re.I)
NOT_DRINK_CAT_RE = re.compile(r"combo|promo|oferta|men[uú]|wrap|pizza|hamburg|burger|pollo|ensalada", re.I)
_cache: Dict[int, Tuple[float, Dict[str, Any]]] = {}


def _stats(partner_id: int) -> Dict[str, Any]:
    """{'count': Counter(item), 'pairs': {item: Counter(otro)}} con caché corta (cambia con cada pedido)."""
    hit = _cache.get(partner_id)
    if hit and time.monotonic() - hit[0] < settings.upsell_cache_seconds:
        return hit[1]
    since = (datetime.now(timezone.utc) - timedelta(days=settings.upsell_days)).isoformat(timespec="seconds")
    count: Counter = Counter()
    pairs: Dict[str, Counter] = defaultdict(Counter)
    for r in db.query("SELECT items FROM orders WHERE partner_id=? AND status!='cancelado' AND created_at>=?",
                      (partner_id, since)):
        ids = sorted({str(i["item_id"]) for i in db.row_dict(r, ("items",))["items"]})
        count.update(ids)
        for a in ids:
            for b in ids:
                if a != b:
                    pairs[a][b] += 1
    out = {"count": count, "pairs": pairs}
    _cache[partner_id] = (time.monotonic(), out)
    return out


def clear_cache(partner_id: Optional[int] = None) -> None:
    if partner_id is None:
        _cache.clear()
    else:
        _cache.pop(partner_id, None)


def drink_ids(menu: Dict[str, Any]) -> List[str]:
    """Bebidas del menú: por categoría y, si no hay categoría de bebidas, por nombre del producto."""
    items = menu["items"]
    out: List[str] = []
    cats = [c for c in menu["categories"]
            if DRINK_CAT_RE.search(c.get("name") or "") and not NOT_DRINK_CAT_RE.search(c.get("name") or "")]
    cats.sort(key=lambda c: 0 if re.search(r"bebida|refresco|gaseosa|drink", c.get("name") or "", re.I) else 1)
    for c in cats:
        out += [i for i in c["items"] if i in items and i not in out]
    if not out:
        out = [i for i, it in items.items() if DRINK_RE.search(it.get("name") or "")]
    return out


def is_drink(menu: Dict[str, Any], item_id: str) -> bool:
    return item_id in set(drink_ids(menu))


def suggest(partner_id: int, menu: Dict[str, Any], item_ids: List[str], context: str = "item",
            limit: int = 3) -> List[Dict[str, Any]]:
    """context='item': lo que se combina con ese producto. 'cart': para cerrar el carrito (bebidas si no hay)."""
    items = menu["items"]
    given = [str(i) for i in item_ids if str(i) in items]
    ok = lambda i: i in items and items[i].get("available") and i not in given  # noqa: E731
    st = _stats(partner_id)
    out: List[Dict[str, Any]] = []

    drinks = [i for i in drink_ids(menu) if ok(i)]
    drinks.sort(key=lambda i: -st["count"][i])  # las más vendidas primero; empate = orden del menú
    has_drink = any(i in set(drink_ids(menu)) for i in given)
    if context == "cart" and not has_drink:
        out += [{"item_id": i, "reason": "bebida"} for i in drinks[:limit]]

    score: Counter = Counter()
    for g in given:
        base = st["count"][g] or 1
        for other, n in st["pairs"].get(g, {}).items():
            if n >= settings.upsell_min_pairs and ok(other):
                score[other] = max(score[other], n / base)
    for other, conf in score.most_common():
        if len(out) >= limit:
            break
        if all(o["item_id"] != other for o in out):
            out.append({"item_id": other, "reason": "combinado", "pct": round(conf * 100)})

    if len(out) < limit and not has_drink:
        for i in drinks:
            if len(out) >= limit:
                break
            if all(o["item_id"] != i for o in out):
                out.append({"item_id": i, "reason": "bebida"})
    return out[:limit]


def top_pairs(partner_id: int, menu: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    """Combinaciones más frecuentes para el admin."""
    st = _stats(partner_id)
    seen, out = set(), []
    flat = [(n, a, b) for a, c in st["pairs"].items() for b, n in c.items() if a < b]
    for n, a, b in sorted(flat, reverse=True):
        if (a, b) in seen:
            continue
        seen.add((a, b))
        name = lambda i: (menu["items"].get(i) or {}).get("name") or i  # noqa: E731
        out.append({"a": name(a), "b": name(b), "orders": n})
        if len(out) >= limit:
            break
    return out

