"""Crear un pedido (web o teléfono): mismo camino, mismos controles. Precio, envío y sucursal los decide el servidor."""
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app import branches, geo, menu as menu_mod, orders, runtime as runtime_mod, upsell
from app.config import settings


async def branch_menu(p: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Menú de la sucursal (la principal es el menú sincronizado del partner)."""
    if b["primary"]:
        m = menu_mod.load(p["id"])
        if not m:
            raise HTTPException(503, "El menú todavía no está disponible")
        return m
    try:
        m = await branches.menu(runtime_mod.get_runtime().source, p, b)
    except Exception:
        m = None
    if not m:
        raise HTTPException(503, "El menú de esta sucursal no está disponible ahora")
    return m


async def place(p: Dict[str, Any], body: Dict[str, Any], channel: str = "web", operator: Optional[str] = None,
                client_ip: Optional[str] = None, before_quote=None) -> Dict[str, Any]:
    if not p["accepting_orders"]:
        raise orders.OrderError("El restaurante no está recibiendo pedidos ahora")
    rt = runtime_mod.get_runtime()
    # La sucursal la decide el servidor con la dirección (como Yango), no el navegador.
    try:
        lat, lon = float((body.get("address") or {})["lat"]), float((body.get("address") or {})["lon"])
    except (KeyError, TypeError, ValueError):
        raise orders.OrderError("Marca tu ubicación en el mapa")
    try:
        b = await branches.find(rt.source, p, lat, lon) if geo.in_bounds(lat, lon) else None
    except Exception:
        raise HTTPException(503, "No pudimos confirmar la sucursal, intenta de nuevo")
    if not b:
        raise orders.OrderError("Por ahora no llegamos a esa dirección")
    if body.get("branch") and body["branch"] != b["slug"]:
        raise orders.OrderError(f"Esa dirección la atiende {b['name']}. Vuelve a elegir tu ubicación para ver su menú.")
    m = await branch_menu(p, b)
    priced = orders.prepare(p, m, body)
    if before_quote:
        before_quote()  # límite por IP (web), después de validar para no gastar cupo en errores
    a = priced["address"]
    try:
        q = await rt.quotes.quote(p["account"], b["slug"], a["lat"], a["lon"])
    except Exception:
        raise HTTPException(503, "No pudimos cotizar el envío, intenta de nuevo")
    summary = orders.check_quote(priced, q, p)
    expected = body.get("expected_total")
    if expected is not None and abs(float(expected) - summary["total"]) > 0.01:
        raise orders.OrderError(f"El total cambió a {settings.currency}{summary['total']:g}. Revisa y confirma de nuevo.")
    o = orders.create(p, priced, summary, client_ip=client_ip, channel=channel, operator=operator)
    upsell.clear_cache(p["id"])
    rt.dispatcher.enqueue(o["account"], o["token"])
    return o
