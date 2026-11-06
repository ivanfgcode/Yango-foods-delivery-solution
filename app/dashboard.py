"""Resumen del negocio para el admin (mismo tablero que el picker de restotec-yango).

Ventas = total cobrado al cliente (productos + envío + servicio) de los pedidos no cancelados. Fechas y horas en la
zona del negocio (TZ_NAME). Todo se calcula en el servidor a partir de la tabla de pedidos.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from app import db, orders, partners
from app.config import settings

PENDING = ("manual", "revision")
IN_FLIGHT = ("en_cola", "colocando")


def summary(days: int = 30, partner_id: Optional[int] = None) -> Dict[str, Any]:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(timezone.utc)
    sql, params = "SELECT * FROM orders WHERE 1=1", []
    if days > 0:
        sql += " AND created_at >= ?"
        # Desde la medianoche local de hace `days - 1` días: el periodo incluye hoy completo.
        start = (now.astimezone(tz) - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        params.append(start.astimezone(timezone.utc).isoformat(timespec="seconds"))
    if partner_id is not None:
        sql += " AND partner_id=?"
        params.append(partner_id)
    rows = [db.row_dict(r, orders.JSON_FIELDS) for r in db.query(sql + " ORDER BY created_at", tuple(params))]
    names = {p["id"]: p["name"] for p in partners.all_partners()}

    status = Counter(o["status"] for o in rows)
    live = [o for o in rows if o["status"] != "cancelado"]
    daily: Dict[str, Dict[str, float]] = defaultdict(lambda: {"orders": 0, "revenue": 0.0, "delivery": 0.0})
    hourly, weekday = [0] * 24, [0.0] * 7
    by_partner: Dict[int, Dict[str, float]] = defaultdict(lambda: {"orders": 0, "revenue": 0.0})
    products: Dict[str, Dict[str, float]] = defaultdict(lambda: {"units": 0, "revenue": 0.0, "orders": 0})
    payments = Counter()
    units = 0
    for o in live:
        local = datetime.fromisoformat(o["created_at"]).astimezone(tz)
        d = daily[local.date().isoformat()]
        d["orders"] += 1
        d["revenue"] += o["total"]
        d["delivery"] += o["delivery_fee"]
        hourly[local.hour] += 1
        weekday[(local.weekday() + 1) % 7] += o["total"]  # 0 = domingo, como en el picker
        bp = by_partner[o["partner_id"]]
        bp["orders"] += 1
        bp["revenue"] += o["total"]
        payments[(o["payment"] or {}).get("method", "cash")] += 1
        for it in o["items"]:
            p = products[it["name"]]
            p["units"] += it["quantity"]
            p["revenue"] += it["amount"]
            p["orders"] += 1
            units += it["quantity"]

    revenue = sum(o["total"] for o in live)
    delivery = sum(o["delivery_fee"] for o in live)
    top = sorted(({"name": k, **v} for k, v in products.items()), key=lambda x: -x["revenue"])
    oldest_pending = min((o["created_at"] for o in rows if o["status"] in PENDING), default=None)
    r2 = lambda v: round(v, 2)  # noqa: E731
    return {
        "days": days,
        "orders": len(live),
        "revenue": r2(revenue),
        "delivery": r2(delivery),
        "avg_ticket": r2(revenue / len(live)) if live else 0,
        "avg_delivery": r2(delivery / len(live)) if live else 0,
        "units": units,
        "products": len(products),
        "status": dict(status),
        "pending": sum(status[s] for s in PENDING),
        "in_flight": sum(status[s] for s in IN_FLIGHT),
        "oldest_pending": oldest_pending,
        "placed_rate": r2(status["colocado"] / len(live) * 100) if live else 0,
        "payments": dict(payments),
        "daily": [{"day": k, "orders": v["orders"], "revenue": r2(v["revenue"]), "delivery": r2(v["delivery"])}
                  for k, v in sorted(daily.items())],
        "hourly": hourly,
        "weekday": [r2(v) for v in weekday],
        "partners": sorted(({"id": k, "name": names.get(k, str(k)), "orders": v["orders"], "revenue": r2(v["revenue"])}
                            for k, v in by_partner.items()), key=lambda x: -x["revenue"]),
        "top": [{**t, "revenue": r2(t["revenue"])} for t in top[:8]],
        "today": now.astimezone(tz).date().isoformat(),
    }
