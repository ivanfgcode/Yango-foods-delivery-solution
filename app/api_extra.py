"""Rutas de cadenas (portal de la cadena), cabina de operadores, menú PDF y comisiones."""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app import (auth, chains, checkout, db, geo, menu as menu_mod, operators, orders, partners, pdfmenu, pricing,
                 themes)
from app.config import ROOT, settings
from fastapi.templating import Jinja2Templates

log = logging.getLogger("api")
router = APIRouter()
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))


def _cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax",
                        secure=settings.public_base_url.startswith("https"))


def _script_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _partner(slug: str) -> Dict[str, Any]:
    p = partners.get(slug)
    if not p:
        raise HTTPException(404, "Restaurante no encontrado")
    return p


def _err(e: Exception) -> HTTPException:
    return HTTPException(422, str(e))


# ============================================================================================ admin: cadenas
@router.get("/api/admin/chains")
async def admin_chains(request: Request):
    auth.require_admin(request)
    return chains.all_chains()


@router.post("/api/admin/chains")
async def admin_create_chain(request: Request, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        c = chains.create(body.get("slug", ""), body.get("name", ""), body.get("account", ""),
                          body.get("login", ""), body.get("password", ""), body.get("contact", ""))
        for slug in body.get("partners") or []:
            chains.attach(c["id"], slug)
    except ValueError as e:
        raise _err(e)
    return c


@router.put("/api/admin/chains/{chain_id}")
async def admin_update_chain(request: Request, chain_id: int, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        return chains.update(chain_id, **{k: body[k] for k in ("name", "contact", "account", "login", "password")
                                          if k in body})
    except ValueError as e:
        raise _err(e)


@router.post("/api/admin/chains/{chain_id}/attach")
async def admin_chain_attach(request: Request, chain_id: int, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        p = chains.attach(chain_id, str(body.get("slug") or ""))
    except ValueError as e:
        raise _err(e)
    return {"slug": p["slug"], "account": p["account"], "chain_id": p["chain_id"]}


@router.post("/api/admin/partners/{slug}/detach")
async def admin_chain_detach(request: Request, slug: str):
    auth.require_admin(request)
    _partner(slug)
    p = chains.detach(slug)
    return {"slug": p["slug"], "account": p["account"], "chain_id": p["chain_id"]}


@router.put("/api/admin/accounts/{name}")
async def admin_account_info(request: Request, name: str, body: Dict[str, Any] = Body(...)):
    """Datos de login de la cuenta Yango del bot (el celular con el que se entra por SMS). No es un secreto."""
    auth.require_admin(request)
    if not db.one("SELECT 1 FROM accounts WHERE name=?", (name,)):
        raise HTTPException(404, "Cuenta no encontrada")
    phone = re.sub(r"[^\d+ ]", "", str(body.get("yango_phone") or ""))[:20]
    db.execute("UPDATE accounts SET yango_phone=? WHERE name=?", (phone or None, name))
    return {"name": name, "yango_phone": phone}


# ============================================================================================ admin: comisiones
@router.get("/api/admin/commissions")
async def admin_commissions(request: Request, month: Optional[str] = None):
    auth.require_admin(request)
    if month and not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(422, "Mes inválido (AAAA-MM)")
    return {"rate": settings.commission_rate, "rows": orders.commission_report(month)}


# ============================================================================================ admin: operadores
@router.get("/api/admin/operators")
async def admin_operators(request: Request):
    auth.require_admin(request)
    today = db.now_iso()[:10]
    return {"operators": operators.all_operators(), "today": operators.stats(today),
            "calls": operators.recent_calls(limit=100)}


@router.post("/api/admin/operators")
async def admin_create_operator(request: Request, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        return operators.create(body.get("login", ""), body.get("name", ""), body.get("password", ""),
                                role=body.get("role") or "operador", schedule=body.get("schedule") or "",
                                phone=body.get("phone") or "", chain_id=body.get("chain_id") or None)
    except ValueError as e:
        raise _err(e)


@router.put("/api/admin/operators/{login}")
async def admin_update_operator(request: Request, login: str, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        return operators.update(login, enabled=body.get("enabled"), password=body.get("password") or None,
                                name=body.get("name"), role=body.get("role"), schedule=body.get("schedule"),
                                phone=body.get("phone"))
    except ValueError as e:
        raise _err(e)


@router.get("/api/admin/team")
async def admin_team(request: Request, days: int = 1, chain: Optional[str] = None):
    """Equipo: cada miembro con horas conectado, llamadas, pedidos, ventas, ticket, conversión y en línea ahora.
    chain: vacío = todos · «central» = equipo de carta.bo · id = equipo de esa cadena."""
    auth.require_admin(request)
    scope: Any = operators.ALL if not chain else None if chain == "central" else int(chain)
    return operators.team(max(1, min(days, 365)), scope)


@router.get("/api/admin/team/{login}")
async def admin_team_member(request: Request, login: str, days: int = 30):
    auth.require_admin(request)
    if not operators.get(login):
        raise HTTPException(404, "Miembro no encontrado")
    return operators.member_detail(login, max(1, min(days, 365)))


@router.get("/api/admin/call-script")
async def admin_get_script(request: Request):
    auth.require_admin(request)
    return {"steps": operators.get_script(), "default": operators.DEFAULT_SCRIPT}


@router.put("/api/admin/call-script")
async def admin_set_script(request: Request, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        return {"steps": operators.set_script(body.get("steps"))}
    except ValueError as e:
        raise _err(e)


# ============================================================================================ admin: menú PDF
@router.get("/api/admin/pdf/{slug}")
async def admin_pdf_get(request: Request, slug: str):
    auth.require_admin(request)
    p = _partner(slug)
    m = menu_mod.load(p["id"])
    return {"style": themes.merge_pdf(p.get("pdf_style") or {}), "categories": pdfmenu.categories(m) if m else [],
            "templates": themes.PDF_TEMPLATES, "fonts": list(themes.FONTS), "choices": themes.PDF_CHOICES,
            "has_menu": bool(m)}


@router.put("/api/admin/pdf/{slug}")
async def admin_pdf_set(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    _partner(slug)
    patch = dict(body)
    if patch.pop("apply_template", False) and patch.get("template") in themes.PDF_TEMPLATES:
        patch = {**themes.PDF_TEMPLATES[patch["template"]], **patch}
    if patch.pop("reset", False):
        db.execute("UPDATE partners SET pdf_style='{}' WHERE slug=?", (slug,))
        return themes.merge_pdf({})
    try:
        return partners.set_pdf_style(slug, patch)
    except (ValueError, TypeError) as e:
        raise _err(e)


def _pdf_html(request: Request, p: Dict[str, Any]) -> str:
    m = menu_mod.load(p["id"]) or {"categories": [], "items": {}}
    ctx = pdfmenu.context(p, m, p.get("pdf_style") or {})
    ctx["base_href"] = str(request.base_url)
    return templates.get_template("menu_pdf.html").render(ctx)


@router.get("/admin/pdf/{slug}/preview", response_class=HTMLResponse)
async def admin_pdf_preview(request: Request, slug: str):
    auth.require_admin(request)
    return HTMLResponse(_pdf_html(request, _partner(slug)))


@router.get("/admin/pdf/{slug}/menu.pdf")
async def admin_pdf_download(request: Request, slug: str):
    auth.require_admin(request)
    p = _partner(slug)
    html = _pdf_html(request, p)
    try:
        data = await asyncio.wait_for(pdfmenu.render_pdf(html, p.get("pdf_style") or {}), timeout=60)
    except Exception as e:
        log.warning("pdf %s: %s", slug, e)
        raise HTTPException(503, "No se pudo generar el PDF en el servidor. Usa «Imprimir» en la vista previa.")
    return Response(data, media_type="application/pdf",
                    headers={"content-disposition": f'attachment; filename="menu-{p["slug"]}.pdf"'})


# ============================================================================================ portal de la cadena
@router.get("/cadena", response_class=HTMLResponse)
async def chain_page(request: Request):
    cid = auth.current_chain(request)
    c = chains.get(cid) if cid else None
    return templates.TemplateResponse(request, "chain.html", {"chain": c, "currency": settings.currency})


@router.post("/api/cadena/login")
async def chain_login(response: Response, body: Dict[str, Any] = Body(...)):
    c = chains.authenticate(str(body.get("login") or ""), str(body.get("password") or ""))
    if not c:
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    _cookie(response, auth.CHAIN_COOKIE, auth.chain_token(c["id"]), auth.TTL)
    return {"ok": True}


@router.post("/api/cadena/logout")
async def chain_logout(response: Response):
    response.delete_cookie(auth.CHAIN_COOKIE)
    return {"ok": True}


@router.get("/api/cadena/me")
async def chain_me(request: Request, month: Optional[str] = None):
    cid = auth.require_chain(request)
    c = chains.get(cid)
    if not c:
        raise HTTPException(401, "La cadena ya no existe")
    locs = chains.locales(cid)
    ids = [p["id"] for p in locs]
    month = month if month and re.match(r"^\d{4}-\d{2}$", month) else db.now_iso()[:7]
    acc = db.one("SELECT status, detail, checked_at FROM accounts WHERE name=?", (c["account"],))
    recent = []
    names = {p["id"]: p["name"] for p in locs}
    for o in orders.list_orders(partner_ids=ids, limit=100):
        v = orders.public_view(o)
        v.update({"partner": names.get(o["partner_id"]), "commission": o.get("commission") or 0,
                  "channel": o.get("channel") or "web",
                  "customer": f"{o['customer']['first_name']} {o['customer']['last_name']}"})
        recent.append(v)
    return {
        "chain": c, "bot": dict(acc) if acc else None, "month": month,
        "locales": [{"slug": p["slug"], "name": p["name"], "enabled": bool(p["enabled"]),
                     "accepting_orders": bool(p["accepting_orders"]), "commission_rate": pricing.commission_rate(p),
                     "url": f"/{p['slug']}", "portal": f"/{p['slug']}/portal"} for p in locs],
        "commissions": orders.commission_report(month, partner_ids=ids),
        "orders": recent,
    }


# ============================================================================================ cadena: su equipo
def _chain_member(request: Request, login: str) -> Dict[str, Any]:
    cid = auth.require_chain(request)
    op = operators.get(login)
    if not op or op.get("chain_id") != cid:
        raise HTTPException(404, "Miembro no encontrado")
    return op


@router.get("/api/cadena/team")
async def chain_team(request: Request, days: int = 1):
    """El equipo de la cadena (solo sus miembros) con sus estadísticas."""
    cid = auth.require_chain(request)
    return operators.team(max(1, min(days, 365)), cid)


@router.get("/api/cadena/team/{login}")
async def chain_team_member(request: Request, login: str, days: int = 30):
    _chain_member(request, login)
    return operators.member_detail(login, max(1, min(days, 365)))


@router.post("/api/cadena/team")
async def chain_team_create(request: Request, body: Dict[str, Any] = Body(...)):
    """La cadena da de alta a su gente: solo atienden los locales de la cadena."""
    cid = auth.require_chain(request)
    try:
        return operators.create(body.get("login", ""), body.get("name", ""), body.get("password", ""),
                                role=body.get("role") or "operador", schedule=body.get("schedule") or "",
                                phone=body.get("phone") or "", chain_id=cid)
    except ValueError as e:
        raise _err(e)


@router.put("/api/cadena/team/{login}")
async def chain_team_update(request: Request, login: str, body: Dict[str, Any] = Body(...)):
    _chain_member(request, login)
    try:
        return operators.update(login, enabled=body.get("enabled"), password=body.get("password") or None,
                                name=body.get("name"), role=body.get("role"), schedule=body.get("schedule"),
                                phone=body.get("phone"))
    except ValueError as e:
        raise _err(e)


# ============================================================================================ cabina de operadores
ADMIN_OP = {"login": "admin", "name": "Administrador", "role": "supervisor", "chain_id": None}


def _op(request: Request) -> Dict[str, Any]:
    """Miembro con sesión en la cabina (el admin entra como supervisor del equipo central, con todo)."""
    login = auth.require_operator(request)
    return dict(ADMIN_OP) if login == "admin" else operators.get(login)


def _supervisor(request: Request) -> Dict[str, Any]:
    op = _op(request)
    if op.get("role") != "supervisor":
        raise HTTPException(403, "Solo para supervisores")
    return op


def _scoped_partner(op: Dict[str, Any], slug: Optional[str]) -> Optional[Dict[str, Any]]:
    """Restaurante dentro del alcance del miembro (los de su cadena, o todos para el equipo central)."""
    if not slug:
        return None
    p = partners.get(str(slug))
    if not p or not p["enabled"] or not operators.in_scope(op, p["id"]):
        raise HTTPException(404, "Restaurante no encontrado")
    return p
@router.get("/operador", response_class=HTMLResponse)
async def operator_page(request: Request):
    op = auth.current_operator(request)
    return templates.TemplateResponse(request, "operator.html", {
        "logged": bool(op), "currency": settings.currency, "phone_prefix": settings.phone_prefix,
        "yandex_key": settings.yandex_maps_api_key})


@router.post("/api/operador/login")
async def operator_login(response: Response, body: Dict[str, Any] = Body(...)):
    op = operators.authenticate(str(body.get("login") or ""), str(body.get("password") or ""))
    if not op:
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    _cookie(response, auth.OPERATOR_COOKIE, auth.operator_token(op["login"]), 12 * 3600)
    operators.touch(op["login"])
    db.event("equipo_entrada", {"login": op["login"]})
    return {"ok": True}


@router.post("/api/operador/logout")
async def operator_logout(request: Request, response: Response):
    login = auth.current_operator(request)
    if login and login != "admin":
        operators.end_session(login)
    response.delete_cookie(auth.OPERATOR_COOKIE)
    return {"ok": True}


@router.get("/api/operador/me")
async def operator_me(request: Request):
    op = _op(request)
    ids = operators.partner_ids(op)
    locs = []
    for p in partners.all_partners():
        if not p["enabled"] or (ids is not None and p["id"] not in ids):
            continue
        locs.append({"slug": p["slug"], "name": p["name"], "accepting_orders": bool(p["accepting_orders"]),
                     "ready": bool(p.get("place_slug")), "center": [p.get("place_lat"), p.get("place_lon")],
                     "address": p.get("place_address"), "hours": (p.get("profile") or {}).get("hours_text"),
                     "chain_id": p.get("chain_id")})
    chain = chains.get(op["chain_id"]) if op.get("chain_id") else None
    return {"operator": {"login": op["login"], "name": op["name"], "role": op.get("role") or "operador",
                         "role_label": operators.ROLES.get(op.get("role") or "operador"),
                         "chain": chain["name"] if chain else None},
            "supervisor": op.get("role") == "supervisor",
            "partners": locs, "script": operators.get_script(), "outcomes": operators.OUTCOMES,
            "tiers": pricing.public_tiers(), "calls": operators.recent_calls(op["login"], limit=15),
            "server_hour": datetime.now(timezone.utc).hour}


@router.post("/api/operador/ping")
async def operator_ping(request: Request):
    """Latido de la cabina (cada minuto): cuenta las horas conectado y devuelve los números del día."""
    login = auth.require_operator(request)
    if login == "admin":
        return {"today": None}
    operators.touch(login)
    st = operators.member_stats(login, 1)
    return {"today": {k: st[k] for k in ("hours_online", "calls", "orders", "sales", "avg_ticket", "first_in")}}


@router.get("/api/operador/customer")
async def operator_customer(request: Request, phone: str):
    op = _op(request)
    try:
        return operators.lookup_customer(phone, operators.partner_ids(op))
    except geo.AddressError as e:
        raise _err(e)


@router.post("/api/operador/calls")
async def operator_start_call(request: Request, body: Dict[str, Any] = Body(default={})):
    op = _op(request)
    p = _scoped_partner(op, body.get("slug"))
    return operators.start_call(op["login"], str(body.get("phone") or ""), p["id"] if p else None)


@router.put("/api/operador/calls/{call_id}")
async def operator_end_call(request: Request, call_id: int, body: Dict[str, Any] = Body(...)):
    op = _op(request)
    login = op["login"]
    p = _scoped_partner(op, body.get("slug"))
    try:
        return operators.end_call(call_id, login, str(body.get("outcome") or ""), str(body.get("notes") or ""),
                                  str(body.get("phone") or ""), p["id"] if p else None,
                                  body.get("order_token") or None)
    except ValueError as e:
        raise _err(e)


@router.post("/api/operador/orders")
async def operator_create_order(request: Request, body: Dict[str, Any] = Body(...)):
    """Pedido telefónico: mismas validaciones y cotización que la web; queda marcado con el operador."""
    op = _op(request)
    p = _scoped_partner(op, body.get("slug") or "-")
    o = await checkout.place(p, body, channel="telefono", operator=op["login"])
    return {"token": o["token"], "number": o["number"], "order": orders.public_view(o),
            "status_url": f"/{p['slug']}/pedido/{o['token']}"}


@router.get("/api/operador/orders")
async def operator_orders(request: Request):
    login = auth.require_operator(request)
    rows = db.query("SELECT token FROM orders WHERE operator=? ORDER BY created_at DESC LIMIT 30", (login,))
    names = {p["id"]: p["name"] for p in partners.all_partners()}
    out = []
    for r in rows:
        o = orders.get(r["token"])
        v = orders.public_view(o)
        v.update({"partner": names.get(o["partner_id"]), "phone": o["customer"]["phone"],
                  "customer": f"{o['customer']['first_name']} {o['customer']['last_name']}"})
        out.append(v)
    return out


# ============================================================================================ cabina: supervisor
@router.get("/api/operador/team")
async def supervisor_team(request: Request, days: int = 1):
    """Supervisor: su equipo (el de su cadena, o el central) con horas, llamadas, pedidos y quién está en línea."""
    op = _supervisor(request)
    return operators.team(max(1, min(days, 365)), op.get("chain_id"))


@router.get("/api/operador/scope-orders")
async def supervisor_orders(request: Request, status: Optional[str] = None):
    """Supervisor: pedidos de los restaurantes que atiende su equipo (web y teléfono), para cancelar o reintentar."""
    op = _supervisor(request)
    if status and status not in orders.STATUSES:
        raise HTTPException(422, "Estado inválido")
    names = {p["id"]: p["name"] for p in partners.all_partners()}
    staff = {o["login"]: o["name"] for o in operators.all_operators()}
    out = []
    for o in orders.list_orders(status=status, limit=60, partner_ids=operators.partner_ids(op)):
        v = orders.public_view(o)
        v.update({"token": o["token"], "partner": names.get(o["partner_id"]), "phone": o["customer"]["phone"],
                  "customer": f"{o['customer']['first_name']} {o['customer']['last_name']}",
                  "channel": o.get("channel") or "web", "operator": staff.get(o.get("operator"), o.get("operator")),
                  "yango_order_nr": o.get("yango_order_nr"), "error": o.get("error"),
                  "yango_comment": o.get("yango_comment")})
        out.append(v)
    return out


@router.post("/api/operador/orders/{token}/status")
async def supervisor_order_status(request: Request, token: str, body: Dict[str, Any] = Body(...)):
    """Supervisor: cancelar, reintentar con el bot o marcar colocado (con el N° de Yango) un pedido de su alcance."""
    op = _supervisor(request)
    o = orders.get(token)
    if not o or not operators.in_scope(op, o["partner_id"]):
        raise HTTPException(404, "Pedido no encontrado")
    o = checkout.order_action(token, str(body.get("action") or ""), str(body.get("yango_order_nr") or ""),
                              by=op["login"])
    return orders.public_view(o)
