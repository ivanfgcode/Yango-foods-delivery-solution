"""Menú del restaurante oculto, leído de food.yango.com (`/api/v2/menu/retrieve/{place_slug}`).

Los ids de platos y opciones son los mismos que usa el carrito de Yango: la web propia vende exactamente
lo que el bot después mete en el carrito. Los precios siempre se recalculan en el servidor con este menú.
"""
import json
from typing import Any, Dict, List, Optional, Tuple

from app import db
from app.config import settings


class MenuError(ValueError):
    pass


def image_url(picture: Optional[Dict[str, Any]], size: str = "450x300") -> Optional[str]:
    uri = (picture or {}).get("uri")
    if not uri:
        return None
    uri = uri.replace("{w}x{h}", size)
    return uri if uri.startswith("http") else settings.yango_base_url + uri


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_menu_retrieve(raw: Dict[str, Any]) -> Dict[str, Any]:
    """JSON de Yango → menú normalizado {categories: [...], items: {id: {...}}}."""
    payload = raw.get("payload", raw)
    categories: List[Dict[str, Any]] = []
    items: Dict[str, Dict[str, Any]] = {}

    def walk(cats: List[Dict[str, Any]]) -> None:
        for c in cats or []:
            ids = []
            for it in c.get("items") or []:
                iid = str(it["id"])
                groups = []
                for g in it.get("optionsGroups") or []:
                    groups.append({
                        "id": str(g["id"]),
                        "name": g.get("name") or "",
                        "required": bool(g.get("required")),
                        "min": int(g.get("minSelected") or 0),
                        "max": int(g.get("maxSelected") or 0),  # 0 = sin límite
                        "options": [{
                            "id": str(o["id"]),
                            "name": (o.get("name") or "").strip(),
                            "price": _num(o.get("decimalPrice", o.get("price"))),
                            "multiplier": int(o.get("multiplier") or 1),
                        } for o in g.get("options") or []],
                    })
                promo = it.get("decimalPromoPrice", it.get("promoPrice"))
                items[iid] = {
                    "id": iid,
                    "public_id": it.get("publicId"),
                    "name": (it.get("name") or "").strip(),
                    "description": (it.get("description") or "").strip(),
                    "price": _num(it.get("decimalPrice", it.get("price"))),
                    "promo_price": _num(promo) if promo is not None else None,
                    "available": bool(it.get("available", True)) and it.get("inStock") != 0,
                    "image": image_url(it.get("picture")),
                    "adult": bool(it.get("adult")),
                    "groups": groups,
                }
                ids.append(iid)
            if ids:
                categories.append({"name": c.get("name") or "", "available": bool(c.get("available", True)),
                                   "items": ids})
            walk(c.get("categories") or [])

    walk(payload.get("categories") or [])
    return {"categories": categories, "items": items}


def save(partner_id: int, menu: Dict[str, Any]) -> None:
    db.execute(
        """INSERT INTO menus(partner_id, data, synced_at, error) VALUES (?,?,?,NULL)
           ON CONFLICT(partner_id) DO UPDATE SET data=excluded.data, synced_at=excluded.synced_at, error=NULL""",
        (partner_id, json.dumps(menu, ensure_ascii=False), db.now_iso()),
    )


def save_error(partner_id: int, error: str) -> None:
    db.execute(
        """INSERT INTO menus(partner_id, data, synced_at, error) VALUES (?, '{}', ?, ?)
           ON CONFLICT(partner_id) DO UPDATE SET error=excluded.error""",
        (partner_id, db.now_iso(), error[:500]),
    )


def load(partner_id: int) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT data, synced_at, error FROM menus WHERE partner_id=?", (partner_id,))
    if not row:
        return None
    menu = json.loads(row["data"] or "{}")
    if not menu.get("items"):
        return None
    menu["synced_at"] = row["synced_at"]
    return menu


def price_line(menu: Dict[str, Any], item_id: str, quantity: int,
               selected: Dict[str, List[str]]) -> Tuple[Dict[str, Any], float]:
    """Valida un plato con sus opciones y devuelve (línea normalizada, importe). Lanza MenuError."""
    item = menu["items"].get(str(item_id))
    if not item:
        raise MenuError("El producto ya no está en el menú")
    if not item["available"]:
        raise MenuError(f"«{item['name']}» no está disponible ahora")
    if not isinstance(quantity, int) or quantity < 1 or quantity > 50:
        raise MenuError("Cantidad inválida")
    selected = {str(k): [str(x) for x in (v or [])] for k, v in (selected or {}).items()}
    groups_by_id = {g["id"]: g for g in item["groups"]}
    unknown = set(selected) - set(groups_by_id)
    if unknown:
        raise MenuError(f"Opciones inválidas para «{item['name']}»")

    unit = item["promo_price"] if item["promo_price"] is not None else item["price"]
    options_out = []
    for g in item["groups"]:
        chosen = selected.get(g["id"], [])
        opts = {o["id"]: o for o in g["options"]}
        if any(c not in opts for c in chosen):
            raise MenuError(f"Opción inválida en «{g['name']}»")
        counts: Dict[str, int] = {}
        for c in chosen:
            counts[c] = counts.get(c, 0) + 1
            if counts[c] > max(1, opts[c]["multiplier"]):
                raise MenuError(f"Demasiadas veces «{opts[c]['name']}»")
        n = len(chosen)
        need = max(g["min"], 1 if g["required"] else 0)
        if n < need:
            raise MenuError(f"Elige {need} en «{g['name']}» ({item['name']})")
        if g["max"] and n > g["max"]:
            raise MenuError(f"Máximo {g['max']} en «{g['name']}» ({item['name']})")
        for c in chosen:
            unit += opts[c]["price"]
            options_out.append({"group_id": g["id"], "group": g["name"], "option_id": c,
                                "name": opts[c]["name"], "price": opts[c]["price"]})
    amount = round(unit * quantity, 2)
    line = {"item_id": item["id"], "name": item["name"], "quantity": quantity,
            "unit_price": round(unit, 2), "amount": amount, "options": options_out}
    return line, amount


def public_menu(menu: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que ve el navegador: menú sin datos internos."""
    return {"categories": menu["categories"], "items": menu["items"], "synced_at": menu.get("synced_at")}
