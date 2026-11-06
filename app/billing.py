"""Cobro de carta.bo: solo comisión por pedido (COMMISSION_RATE, 3 % del subtotal), que el restaurante paga a Yango.

Sin cuota mensual, sin instalación y sin permanencia. Los pedidos cancelados no generan comisión. Aquí se arma el
resumen que ve el restaurante (mes en curso e historial) y se guarda la aceptación del acuerdo de servicio.
"""
from typing import Any, Dict, List, Optional

from app import branches, db, orders, pricing
from app.config import settings

# Subir la versión cuando cambie el texto del acuerdo (app/templates/portal.html): se vuelve a pedir la aceptación.
TERMS_VERSION = "2026-11-comision"


def _row(p: Dict[str, Any]) -> Dict[str, Any]:
    row = db.one("SELECT * FROM billing WHERE partner_id=?", (p["id"],))
    if not row:
        # Las columnas del plan anterior quedan en 0: solo se usa la aceptación del acuerdo.
        db.execute("""INSERT INTO billing(partner_id, price_bs, branches, sponsored_months, min_paid_months, start_date,
                      updated_at) VALUES (?,?,?,?,?,?,?)""",
                   (p["id"], 0, None, 0, 0, (p.get("created_at") or db.now_iso())[:10], db.now_iso()))
        row = db.one("SELECT * FROM billing WHERE partner_id=?", (p["id"],))
    return dict(row)


def branch_list(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Sucursales del partner que conocemos: la principal y las que Yango asignó a algún cliente."""
    out = []
    main = branches.primary(p)
    if main:
        out.append({"slug": main["slug"], "name": main["name"], "address": main.get("address"), "primary": True})
    for r in db.query("SELECT place_slug, name, address FROM branches WHERE partner_id=? ORDER BY name", (p["id"],)):
        out.append({"slug": r["place_slug"], "name": r["name"], "address": r["address"], "primary": False})
    return out


def summary(p: Dict[str, Any]) -> Dict[str, Any]:
    b = _row(p)
    rate = pricing.commission_rate(p)
    month = db.now_iso()[:7]
    months = orders.commission_report(partner_ids=[p["id"]])
    cur = next((m for m in months if m["month"] == month), None) or {"orders": 0, "subtotal": 0, "commission": 0}
    accepted = bool(b["accepted_at"]) and b["terms_version"] == TERMS_VERSION
    return {
        "model": "comision", "commission_rate": rate, "commission_pct": round(rate * 100, 2),
        "currency": settings.currency, "month": month,
        "month_orders": cur["orders"], "month_sales": cur["subtotal"] or 0, "month_commission": cur["commission"] or 0,
        "months": [{k: m[k] for k in ("month", "orders", "subtotal", "commission")} for m in months],
        "known_branches": branch_list(p), "branches": max(1, len(branch_list(p))),
        "state": "activo" if accepted else "sin_aceptar",
        "accepted_at": b["accepted_at"], "accepted_name": b["accepted_name"], "terms_version": TERMS_VERSION,
        "terms_current": b["terms_version"] == TERMS_VERSION, "payment_info": settings.commission_payment_info,
    }


def accept(p: Dict[str, Any], name: str, ip: Optional[str]) -> Dict[str, Any]:
    name = " ".join(str(name or "").split())[:80]
    if len(name) < 5:
        raise ValueError("Escribe tu nombre completo para aceptar el acuerdo")
    _row(p)
    db.execute("UPDATE billing SET accepted_at=?, accepted_name=?, accepted_ip=?, terms_version=?, updated_at=? WHERE partner_id=?",
               (db.now_iso(), name, (ip or "")[:64], TERMS_VERSION, db.now_iso(), p["id"]))
    db.event("acuerdo_aceptado", {"nombre": name, "version": TERMS_VERSION}, partner_id=p["id"])
    return summary(p)
