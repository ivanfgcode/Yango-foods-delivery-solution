"""Plan de Menu Deli Bo: un precio mensual por sucursal, los primeros meses pagados por Yango y una permanencia
mínima después. El cobro es manual (QR o transferencia): el equipo marca hasta qué día está pagado.

Ejemplo por defecto: Bs150 por sucursal al mes; meses 1-3 los paga Yango; meses 4-6 de permanencia mínima.
"""
import calendar
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app import branches, db
from app.config import settings

TERMS_VERSION = "2026-10"


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _row(p: Dict[str, Any]) -> Dict[str, Any]:
    row = db.one("SELECT * FROM billing WHERE partner_id=?", (p["id"],))
    if not row:
        start = (p.get("created_at") or db.now_iso())[:10]
        db.execute("""INSERT INTO billing(partner_id, price_bs, branches, sponsored_months, min_paid_months, start_date,
                      updated_at) VALUES (?,?,?,?,?,?,?)""",
                   (p["id"], settings.plan_price_bs, None, settings.plan_sponsored_months,
                    settings.plan_min_paid_months, start, db.now_iso()))
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
    known = branch_list(p)
    count = max(1, b["branches"] or len(known))
    monthly = round(b["price_bs"] * count, 2)
    start = date.fromisoformat(b["start_date"])
    sponsored_end = add_months(start, b["sponsored_months"])            # primer día que paga el partner
    commitment_end = add_months(sponsored_end, b["min_paid_months"])     # desde aquí puede dar de baja
    paid_until = date.fromisoformat(b["paid_until"]) if b["paid_until"] else None
    free = bool(b.get("free_forever"))
    now = today()

    schedule = []
    for i in range(b["sponsored_months"] + b["min_paid_months"] + 3):
        p_start, p_end = add_months(start, i), add_months(start, i + 1)
        sponsored = i < b["sponsored_months"]
        if free and not sponsored:
            status = "cortesia"
        elif sponsored:
            status = "patrocinado"
        elif paid_until and paid_until >= p_end:
            status = "pagado"
        elif p_start <= now:
            status = "por_pagar"
        else:
            status = "proximo"
        schedule.append({"month": i + 1, "from": p_start.isoformat(), "to": p_end.isoformat(),
                         "amount": 0 if sponsored or free else monthly, "list_amount": monthly, "status": status,
                         "sponsored": sponsored, "free": free and not sponsored,
                         "committed": not sponsored and not free and p_start < commitment_end})

    due = [s for s in schedule if s["status"] == "por_pagar"]
    if not b["accepted_at"]:
        state = "sin_aceptar"
    elif free:
        state = "cortesia"
    elif now < sponsored_end:
        state = "patrocinado"
    elif due:
        state = "pago_pendiente"
    else:
        state = "activo"
    return {
        "plan": "Menu Deli Bo · Pro", "price_bs": b["price_bs"], "branches": count, "branches_contracted": b["branches"],
        "known_branches": known, "monthly_bs": monthly, "currency": settings.currency,
        "sponsored_months": b["sponsored_months"], "min_paid_months": b["min_paid_months"],
        "start_date": start.isoformat(), "sponsored_until": sponsored_end.isoformat(),
        "commitment_until": commitment_end.isoformat(), "paid_until": b["paid_until"],
        "sponsored_value_bs": round(monthly * b["sponsored_months"], 2),
        "days_left_sponsored": max(0, (sponsored_end - now).days), "sponsored_total_days": max(1, (sponsored_end - start).days),
        "state": state, "due_bs": round(sum(s["amount"] for s in due), 2), "schedule": schedule,
        "accepted_at": b["accepted_at"], "accepted_name": b["accepted_name"], "terms_version": TERMS_VERSION,
        "terms_current": b["terms_version"] == TERMS_VERSION,
        "free_forever": free, "free_note": b.get("free_note") or "",
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


def update(p: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    """Equipo: precio, sucursales contratadas, meses patrocinados, permanencia, inicio y hasta cuándo está pagado."""
    _row(p)
    sets, vals = [], []
    if "price_bs" in fields:
        sets.append("price_bs=?"); vals.append(max(0.0, min(10000.0, float(fields["price_bs"]))))
    if "branches" in fields:
        n = fields["branches"]
        sets.append("branches=?"); vals.append(None if n in (None, "", 0) else max(1, min(200, int(n))))
    for k, hi in (("sponsored_months", 60), ("min_paid_months", 36)):
        if k in fields:
            sets.append(f"{k}=?"); vals.append(max(0, min(hi, int(fields[k]))))
    if "extend_months" in fields:  # alarga los meses gratis: el primer cobro se corre esos meses
        n = max(-24, min(24, int(fields["extend_months"])))
        sets.append("sponsored_months=MAX(0, MIN(60, sponsored_months + ?))"); vals.append(n)
    if "free_forever" in fields:
        sets.append("free_forever=?"); vals.append(1 if fields["free_forever"] else 0)
    if "free_note" in fields:
        sets.append("free_note=?"); vals.append(" ".join(str(fields["free_note"] or "").split())[:200] or None)
    for k in ("start_date", "paid_until"):
        if k in fields:
            v = fields[k] or None
            if v is not None:
                v = date.fromisoformat(str(v)[:10]).isoformat()
            sets.append(f"{k}=?"); vals.append(v)
    if sets:
        db.execute(f"UPDATE billing SET {', '.join(sets)}, updated_at=? WHERE partner_id=?", (*vals, db.now_iso(), p["id"]))
        db.event("plan_actualizado", {k: fields[k] for k in fields}, partner_id=p["id"])
    return summary(p)


