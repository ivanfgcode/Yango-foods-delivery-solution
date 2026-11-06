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
        return operators.create(body.get("login", ""), body.get("name", ""), body.get("password", ""))
    except ValueError as e:
        raise _err(e)


@router.put("/api/admin/operators/{login}")
async def admin_update_operator(request: Request, login: str, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        return operators.update(login, enabled=body.get("enabled"), password=body.get("password") or None,
                                name=body.get("name"))
    except ValueError as e:
        raise _err(e)


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


# ============================================================================================ cabina de operadores
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
    return {"ok": True}


@router.post("/api/operador/logout")
async def operator_logout(response: Response):
    response.delete_cookie(auth.OPERATOR_COOKIE)
    return {"ok": True}


@router.get("/api/operador/me")
async def operator_me(request: Request):
    login = auth.require_operator(request)
    op = operators.get(login) if login != "admin" else {"login": "admin", "name": "Administrador"}
    locs = []
    for p in partners.all_partners():
        if not p["enabled"]:
            continue
        locs.append({"slug": p["slug"], "name": p["name"], "accepting_orders": bool(p["accepting_orders"]),
                     "ready": bool(p.get("place_slug")), "center": [p.get("place_lat"), p.get("place_lon")],
                     "address": p.get("place_address"), "hours": (p.get("profile") or {}).get("hours_text"),
                     "chain_id": p.get("chain_id")})
    return {"operator": op, "partners": locs, "script": operators.get_script(), "outcomes": operators.OUTCOMES,
            "tiers": pricing.public_tiers(), "calls": operators.recent_calls(login, limit=15),
            "server_hour": datetime.now(timezone.utc).hour}


@router.get("/api/operador/customer")
async def operator_customer(request: Request, phone: str):
    auth.require_operator(request)
    try:
        return operators.lookup_customer(phone)
    except geo.AddressError as e:
        raise _err(e)


@router.post("/api/operador/calls")
async def operator_start_call(request: Request, body: Dict[str, Any] = Body(default={})):
    login = auth.require_operator(request)
    p = partners.get(str(body.get("slug") or "")) if body.get("slug") else None
    return operators.start_call(login, str(body.get("phone") or ""), p["id"] if p else None)


@router.put("/api/operador/calls/{call_id}")
async def operator_end_call(request: Request, call_id: int, body: Dict[str, Any] = Body(...)):
    login = auth.require_operator(request)
    p = partners.get(str(body.get("slug") or "")) if body.get("slug") else None
    try:
        return operators.end_call(call_id, login, str(body.get("outcome") or ""), str(body.get("notes") or ""),
                                  str(body.get("phone") or ""), p["id"] if p else None,
                                  body.get("order_token") or None)
    except ValueError as e:
        raise _err(e)


@router.post("/api/operador/orders")
async def operator_create_order(request: Request, body: Dict[str, Any] = Body(...)):
    """Pedido telefónico: mismas validaciones y cotización que la web; queda marcado con el operador."""
    login = auth.require_operator(request)
    p = partners.get(str(body.get("slug") or ""))
    if not p or not p["enabled"]:
        raise HTTPException(404, "Restaurante no encontrado")
    o = await checkout.place(p, body, channel="telefono", operator=login)
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
