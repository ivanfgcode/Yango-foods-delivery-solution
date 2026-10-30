"""Seguimiento de pedidos ya colocados en Yango y avisos al cliente.

De dónde sale el estado (como en restotec-yango, bot/sources/yango_web.py):
  * Vendor app del local (vendor.yango.com) si el partner tiene `vendor_account` con sesión:
    GET /4.0/restapp-front/eats-restapp-orders/v2/active → orders_by_status[].orders[] {order_nr, status, ...}
    (new | accepted | ready | in_delivery). Si el pedido sale de la lista, el detalle
    /eats-restapp-orders/v1/order?order_nr= dice cómo terminó (released/given/delivered o refused/cancelled).
  * Si no, la cuenta cliente que colocó el pedido (food.yango.com, seguimiento del pedido).
Cada cambio de etapa se guarda en el pedido y se avisa por Web Push a los teléfonos suscritos."""
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from app import alerts, db, orders, partners, push
from app.config import settings
from bot.base import SessionDown

log = logging.getLogger("tracking")

STAGES = ("recibido", "preparando", "listo", "en_camino", "entregado", "cancelado")
FINAL = ("entregado", "cancelado")
# Título y texto de la notificación (y de la página del pedido).
TEXT = {
    "confirmado": ("Pedido confirmado", "Tu pedido ya está en el restaurante."),
    "recibido": ("El restaurante recibió tu pedido", "Te avisamos cuando empiecen a prepararlo."),
    "preparando": ("Están preparando tu pedido", "El restaurante ya está cocinando."),
    "listo": ("Tu pedido está listo", "Esperando al mensajero de Yango."),
    "en_camino": ("Tu pedido va en camino", "El mensajero de Yango ya lo lleva. Ten a mano el efectivo o el QR."),
    "entregado": ("Pedido entregado", "Que lo disfrutes."),
    "cancelado": ("Pedido cancelado", "El pedido se canceló en Yango. Si ya pagaste algo, escríbenos."),
}
# Estados crudos conocidos. Vendor app verificados (restotec-yango, 2026-10-04/07): new, accepted, ready, in_delivery,
# given (entregado al mensajero), released, refused. El resto se reconoce por palabras (cuenta cliente: por verificar).
KNOWN = {
    "new": "recibido", "created": "recibido", "call_center_confirmed": "recibido", "place_confirmed": "recibido",
    "accepted": "preparando", "cooking": "preparando", "confirmed": "preparando",
    "ready": "listo", "ready_for_delivery": "listo", "packed": "listo",
    "in_delivery": "en_camino", "given": "en_camino", "taken": "en_camino", "courier_picked_up": "en_camino",
    "picked_up": "en_camino", "delivering": "en_camino", "on_the_way": "en_camino",
    "arrived_to_customer": "en_camino",
    "delivered": "entregado", "released": "entregado", "finished": "entregado", "complete": "entregado",
    "completed": "entregado", "closed": "entregado",
    "refused": "cancelado", "rejected": "cancelado", "cancelled": "cancelado", "canceled": "cancelado",
}


def stage_of(raw: Any) -> Optional[str]:
    low = str(raw or "").strip().lower()
    if not low:
        return None
    if low in KNOWN:
        return KNOWN[low]
    for words, stage in (("cancel|refus|reject", "cancelado"), ("deliver|releas|finish|complet", "entregado"),
                         ("courier|transit|way|taken|pick", "en_camino"), ("ready|pack", "listo"),
                         ("cook|accept|prepar", "preparando"), ("new|creat|confirm", "recibido")):
        if re.search(words, low):
            return stage
    return None


def _dicts(obj: Any, depth: int = 0) -> Iterator[dict]:
    if depth > 8:
        return
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _dicts(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from _dicts(v, depth + 1)


def parse_vendor_active(body: Any) -> Dict[str, Dict[str, Any]]:
    """order_nr → {raw, stage, eta} de los pedidos activos de la vendor app."""
    out: Dict[str, Dict[str, Any]] = {}
    for group in (body or {}).get("orders_by_status") or [] if isinstance(body, dict) else []:
        for o in (group or {}).get("orders") or []:
            nr = str(o.get("order_nr") or "")
            raw = str(o.get("status") or group.get("status") or "")
            if nr and stage_of(raw):
                out[nr] = {"raw": raw, "stage": stage_of(raw), "eta": o.get("courier_approximate_arrival_time")}
    return out


def parse_vendor_final(body: Any) -> Optional[Dict[str, Any]]:
    """Del detalle de un pedido que ya no está activo: {raw, stage} o None."""
    if not isinstance(body, dict):
        return None
    raw = str(body.get("status") or body.get("order_status") or "").strip()
    return {"raw": raw, "stage": stage_of(raw)} if stage_of(raw) else None


def find_status(body: Any, order_nr: str) -> Optional[str]:
    """Busca el pedido por su número en cualquier JSON (seguimiento de la cuenta cliente) y devuelve su estado crudo."""
    for d in _dicts(body):
        if order_nr not in [str(v) for v in d.values() if isinstance(v, (str, int))]:
            continue
        for node in [d] + [v for v in d.values() if isinstance(v, dict)]:
            for key in ("status", "order_status", "state"):
                v = node.get(key)
                if isinstance(v, dict):
                    v = v.get("id") or v.get("code") or v.get("status")
                if isinstance(v, str) and v:
                    return v
    # El número suele ir en order.orderNr y el estado en un hermano (status.id).
    for d in _dicts(body):
        inner = [v for v in d.values() if isinstance(v, dict)]
        if any(order_nr in [str(x) for x in i.values() if isinstance(x, (str, int))] for i in inner):
            st = d.get("status")
            if isinstance(st, dict):
                st = st.get("id") or st.get("code")
            if isinstance(st, str) and st:
                return st
    return None


def apply(token: str, stage: str, raw: str, source: str, eta: Optional[str] = None) -> bool:
    """Guarda la etapa si avanza (nunca retrocede, salvo cancelado). True si cambió."""
    o = orders.get(token)
    if not o or stage not in STAGES:
        return False
    cur = o.get("track_stage")
    if cur == stage or cur in FINAL:
        return False
    if cur and stage != "cancelado" and STAGES.index(stage) < STAGES.index(cur):
        return False
    db.execute("UPDATE orders SET track_stage=?, track_raw=?, track_at=?, track_eta=?, updated_at=? WHERE token=?",
               (stage, raw[:60], db.now_iso(), eta, db.now_iso(), token))
    db.event(f"seguimiento_{stage}", {"raw": raw, "source": source}, order_token=token,
             partner_id=o["partner_id"], account=o["account"])
    return True


async def notify(token: str, stage: str) -> int:
    o = orders.get(token)
    if not o:
        return 0
    p = partners.get_by_id(o["partner_id"])
    title, body = TEXT[stage]
    icon = ((p.get("theme") or {}).get("logo_url") if isinstance(p.get("theme"), dict) else None) or None
    return await push.send(token, f"{p['name']} · {title}", f"Pedido #{o['number']}. {body}",
                           f"/{p['slug']}/pedido/{token}", icon)


def open_orders() -> List[Dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(hours=settings.tracking_hours)).isoformat(timespec="seconds")
    rows = db.query("""SELECT token FROM orders WHERE status='colocado' AND yango_order_nr IS NOT NULL
                       AND (track_stage IS NULL OR track_stage NOT IN ('entregado','cancelado')) AND created_at>=?""",
                    (since,))
    return [orders.get(r["token"]) for r in rows]


_down: Dict[str, str] = {}


async def poll_once(source: Any) -> int:
    """Lee el estado de los pedidos abiertos. Una consulta por cuenta vendor (o cliente) y por vuelta."""
    by_vendor: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_account: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for o in open_orders():
        p = partners.get_by_id(o["partner_id"])
        (by_vendor[p["vendor_account"]] if p.get("vendor_account") else by_account[o["account"]]).append(o)
    changes: List[tuple] = []
    for acc, items in by_vendor.items():
        try:
            active = parse_vendor_active(await source.vendor_active(acc))
            _down.pop(acc, None)
        except SessionDown as e:
            if _down.get(acc) != str(e):
                _down[acc] = str(e)
                await alerts.alert("sesion_caida", f"[VENDOR] Cuenta {acc} sin sesión en la vendor app ({e}). "
                                                   "Sin ella no se ve el estado de los pedidos. Cargar cookies en Admin → Cuentas.")
            continue
        except Exception as e:
            log.warning("vendor %s: %s", acc, e)
            continue
        for o in items:
            nr = o["yango_order_nr"]
            if nr in active:
                a = active[nr]
                changes.append((o, a["stage"], a["raw"], "vendor", a["eta"]))
            elif o.get("track_stage") or _age_min(o) > 3:
                # Ya no está activo: el detalle dice si se entregó o se canceló.
                try:
                    fin = parse_vendor_final(await source.vendor_order(acc, nr))
                except Exception as e:
                    log.info("detalle vendor %s: %s", nr, e)
                    fin = None
                if fin:
                    changes.append((o, fin["stage"], fin["raw"], "vendor", None))
    for acc, items in by_account.items():
        try:
            body = await source.order_tracking(acc)
        except Exception as e:
            log.info("seguimiento cliente %s: %s", acc, e)
            continue
        for o in items:
            raw = find_status(body, o["yango_order_nr"])
            if raw and stage_of(raw):
                changes.append((o, stage_of(raw), raw, "cliente", None))
    n = 0
    for o, stage, raw, src, eta in changes:
        if apply(o["token"], stage, raw, src, eta):
            n += 1
            await notify(o["token"], stage)
            if stage == "cancelado":
                await alerts.alert("pedido_cancelado", f"[CANCELADO] Pedido #{o['number']} ({o['yango_order_nr']}) "
                                                       f"cancelado en Yango ({raw}).")
    return n


def _age_min(o: Dict[str, Any]) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(o["created_at"])).total_seconds() / 60
    except (TypeError, ValueError):
        return 0.0
