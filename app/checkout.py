"""Crear un pedido (web o teléfono): mismo camino, mismos controles. Precio y envío los calcula el servidor."""
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import menu as menu_mod, orders, runtime as runtime_mod
from app.config import settings


async def place(p: Dict[str, Any], body: Dict[str, Any], channel: str = "web", operator: Optional[str] = None,
                client_ip: Optional[str] = None, before_quote=None) -> Dict[str, Any]:
    if not p["accepting_orders"]:
        raise orders.OrderError("El restaurante no está recibiendo pedidos ahora")
    m = menu_mod.load(p["id"])
    if not m:
        raise HTTPException(503, "El menú todavía no está disponible")
    priced = orders.prepare(p, m, body)
    if before_quote:
        before_quote()  # límite por IP (web), después de validar para no gastar cupo en errores
    a = priced["address"]
    rt = runtime_mod.get_runtime()
    try:
        q = await rt.quotes.quote(p["account"], p["place_slug"], a["lat"], a["lon"])
    except Exception:
        raise HTTPException(503, "No pudimos cotizar el envío, intenta de nuevo")
    summary = orders.check_quote(priced, q, p)
    expected = body.get("expected_total")
    if expected is not None and abs(float(expected) - summary["total"]) > 0.01:
        raise orders.OrderError(f"El total cambió a {settings.currency}{summary['total']:g}. Revisa y confirma de nuevo.")
    o = orders.create(p, priced, summary, client_ip=client_ip, channel=channel, operator=operator)
    rt.dispatcher.enqueue(o["account"], o["token"])
    return o
