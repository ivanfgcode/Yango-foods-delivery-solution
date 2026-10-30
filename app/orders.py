"""Pedidos de la web propia. El precio se recalcula siempre con el menú sincronizado y la cotización de Yango."""
import json
import re
import secrets
from typing import Any, Dict, List, Optional

from app import db, geo, menu as menu_mod, quotes
from app.config import settings

# en_cola → colocando → colocado            (el bot lo creó en Yango)
#                     ↘ manual              (bot apagado o falló: un operador lo carga a mano en Yango)
#                     ↘ revision            (Yango cobra distinto de lo cotizado, plato agotado, etc.)
# cualquiera → cancelado
STATUSES = ("en_cola", "colocando", "colocado", "manual", "revision", "cancelado")
OPEN_STATUSES = ("en_cola", "colocando", "manual", "revision")
PAYMENTS = {"cash": "Efectivo al mensajero", "qr": "QR al mensajero"}
JSON_FIELDS = ("customer", "address", "items", "payment", "quote")
CUSTOMER_STATUS = {
    "en_cola": "Recibimos tu pedido",
    "colocando": "Enviando tu pedido al restaurante",
    "colocado": "Pedido confirmado: el restaurante lo está preparando",
    "manual": "Recibimos tu pedido, lo estamos confirmando",
    "revision": "Estamos revisando tu pedido, te contactaremos",
    "cancelado": "Pedido cancelado",
}


class OrderError(ValueError):
    pass


def _clean_name(v: Any, field: str) -> str:
    s = re.sub(r"\s+", " ", re.sub(r"[<>|]", "", str(v or ""))).strip()
    if len(s) < 2:
        raise OrderError(f"Escribe tu {field}")
    return s[:40]


def build_comment(customer: Dict[str, str], address: Dict[str, Any], payment: Dict[str, Any], number: str) -> str:
    """Comentario del pedido en Yango: el mensajero ve quién es el cliente real y cómo paga."""
    pay = PAYMENTS[payment["method"]]
    if payment["method"] == "cash" and payment.get("change_for"):
        pay += f" (paga con {settings.currency}{payment['change_for']:g})"
    parts = [
        f"CLIENTE: {customer['first_name']} {customer['last_name']}",
        f"TEL: {customer['phone']}",
        f"PAGO: {pay}",
        f"REF: {address['reference']}",
    ]
    if address.get("comment"):
        parts.append(f"NOTA: {address['comment']}")
    parts.append(f"WEB #{number}")
    return " | ".join(parts)[:480]


def price_cart(menu: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        raise OrderError("El carrito está vacío")
    if len(items) > 40:
        raise OrderError("Demasiados productos")
    lines, subtotal = [], 0.0
    for it in items:
        try:
            line, amount = menu_mod.price_line(menu, str(it.get("item_id")), int(it.get("quantity") or 0),
                                               it.get("options") or {})
        except (menu_mod.MenuError, ValueError, TypeError) as e:
            raise OrderError(str(e))
        lines.append(line)
        subtotal += amount
    return {"lines": lines, "subtotal": round(subtotal, 2)}


def prepare(partner: Dict[str, Any], menu: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """Valida todo lo que no depende de Yango. Devuelve customer, address, payment, lines, subtotal."""
    c = body.get("customer") or {}
    try:
        customer = {
            "first_name": _clean_name(c.get("first_name"), "nombre"),
            "last_name": _clean_name(c.get("last_name"), "apellido"),
            "phone": geo.normalize_phone(str(c.get("phone") or "")),
        }
        address = geo.validate_address(body.get("address") or {})
    except geo.AddressError as e:
        raise OrderError(str(e))
    p = body.get("payment") or {}
    method = p.get("method")
    if method not in PAYMENTS:
        raise OrderError("Elige cómo vas a pagar al mensajero")
    payment: Dict[str, Any] = {"method": method, "label": PAYMENTS[method]}
    if method == "cash" and p.get("change_for") not in (None, ""):
        try:
            payment["change_for"] = round(float(p["change_for"]), 2)
        except (TypeError, ValueError):
            raise OrderError("Monto de cambio inválido")
    cart = price_cart(menu, body.get("items") or [])
    return {"customer": customer, "address": address, "payment": payment, **cart}


def check_quote(priced: Dict[str, Any], quote: Dict[str, Any], partner: Dict[str, Any]) -> Dict[str, Any]:
    summary = quotes.price_for(quote, priced["subtotal"])
    if not quote["available"]:
        raise OrderError({"fuera_de_zona": "El restaurante no entrega en esa dirección",
                          "cerrado": "El restaurante está cerrado ahora"}.get(quote["reason"] or "",
                                                                                "El restaurante no está disponible"))
    min_order = max(quote.get("min_order") or 0, partner.get("min_order") or 0)
    if priced["subtotal"] < min_order:
        raise OrderError(f"El pedido mínimo es {settings.currency}{min_order:g}")
    if summary["delivery_fee"] is None:
        raise OrderError("No se pudo calcular el envío para este pedido")
    return summary


def new_number() -> str:
    alphabet = "ACDEFGHJKMNPQRTUVWXY34679"
    return "".join(secrets.choice(alphabet) for _ in range(6))


def create(partner: Dict[str, Any], priced: Dict[str, Any], summary: Dict[str, Any],
           client_ip: Optional[str] = None) -> Dict[str, Any]:
    token = secrets.token_urlsafe(16)
    now = db.now_iso()
    for _ in range(5):
        number = new_number()
        if not db.one("SELECT 1 FROM orders WHERE number=?", (number,)):
            break
    comment = build_comment(priced["customer"], priced["address"], priced["payment"], number)
    quote_keep = {k: summary.get(k) for k in ("delivery_fee", "service_fee", "total", "eta_min", "eta_max",
                                              "distance_km", "surge", "thresholds", "min_order", "place_slug")}
    db.execute(
        """INSERT INTO orders(token, number, partner_id, account, status, customer, address, items, payment,
           subtotal, delivery_fee, service_fee, total, quote, yango_comment, client_ip, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (token, number, partner["id"], partner["account"], "en_cola",
         json.dumps(priced["customer"], ensure_ascii=False), json.dumps(priced["address"], ensure_ascii=False),
         json.dumps(priced["lines"], ensure_ascii=False), json.dumps(priced["payment"], ensure_ascii=False),
         priced["subtotal"], summary["delivery_fee"], summary.get("service_fee") or 0, summary["total"],
         json.dumps(quote_keep, ensure_ascii=False), comment, client_ip, now, now),
    )
    db.event("pedido_nuevo", {"number": number, "total": summary["total"]}, order_token=token,
             partner_id=partner["id"], account=partner["account"])
    return get(token)


def get(token: str) -> Optional[Dict[str, Any]]:
    return db.row_dict(db.one("SELECT * FROM orders WHERE token=?", (token,)), JSON_FIELDS)


def set_status(token: str, status: str, **fields: Any) -> Dict[str, Any]:
    assert status in STATUSES
    allowed = {"yango_order_nr", "yango_total", "error", "attempts"}
    sets, vals = ["status=?", "updated_at=?"], [status, db.now_iso()]
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    db.execute(f"UPDATE orders SET {', '.join(sets)} WHERE token=?", (*vals, token))
    o = get(token)
    db.event(f"estado_{status}", {k: v for k, v in fields.items() if k in allowed}, order_token=token,
             partner_id=o["partner_id"], account=o["account"])
    return o


def list_orders(partner_id: Optional[int] = None, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    sql, params = "SELECT * FROM orders WHERE 1=1", []
    if partner_id is not None:
        sql += " AND partner_id=?"
        params.append(partner_id)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return [db.row_dict(r, JSON_FIELDS) for r in db.query(sql, tuple(params))]


def public_view(o: Dict[str, Any]) -> Dict[str, Any]:
    from app.tracking import TEXT
    stage = o.get("track_stage") if o["status"] == "colocado" else None
    return {
        "number": o["number"], "status": o["status"],
        "status_text": TEXT[stage][0] if stage else CUSTOMER_STATUS[o["status"]],
        "stage": stage, "stage_text": TEXT[stage][1] if stage else None, "courier_eta": o.get("track_eta"),
        "items": [{"name": i["name"], "quantity": i["quantity"], "amount": i["amount"],
                   "options": [x["name"] for x in i["options"]]} for i in o["items"]],
        "subtotal": o["subtotal"], "delivery_fee": o["delivery_fee"], "service_fee": o["service_fee"],
        "total": o["total"], "payment": o["payment"]["label"],
        "address": o["address"]["text"], "reference": o["address"]["reference"],
        "eta_min": (o["quote"] or {}).get("eta_min"), "eta_max": (o["quote"] or {}).get("eta_max"),
        "created_at": o["created_at"],
    }
