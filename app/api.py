"""API y páginas: tienda pública (dominio.com/<slug>), portal del partner y panel de admin."""
import asyncio
import hmac
import json
import logging
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, Deque, Dict, Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth, db, geo, menu as menu_mod, orders, partners, quotes, runtime as runtime_mod, themes
from app.config import ROOT, settings
from bot import sessions

log = logging.getLogger("api")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))


def seed_demo() -> None:
    if partners.all_partners():
        return
    partners.create("wrap-roll", "Wrap & Roll", "cuenta_demo",
                    yango_url="https://food.yango.com/bo/santa-cruz-de-la-sierra/r/wrap_roll_-_san_martin",
                    password="demo12345",
                    theme={"primary": "#F25C54"},
                    profile={"tagline": "Wraps frescos a tu puerta", "hours_text": "Todos los días 11:00 – 22:00",
                             "announcement": "-20% en combos de hoy"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    db.connect()
    if settings.demo_seed:
        seed_demo()
    rt = runtime_mod.Runtime()
    runtime_mod.runtime = rt
    await rt.start(background=getattr(app.state, "background", True))
    yield
    await rt.stop()


app = FastAPI(title="Yango Foods · Pedidos propios", lifespan=lifespan)
app.state.background = True
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
settings.ensure_dirs()
app.mount("/uploads", StaticFiles(directory=str(settings.uploads_dir)), name="uploads")


def script_json(data: Any) -> str:
    """JSON para incrustar en <script> sin poder cerrar la etiqueta."""
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def rt() -> runtime_mod.Runtime:
    return runtime_mod.get_runtime()


def _partner_or_404(slug: str, public: bool = True) -> Dict[str, Any]:
    p = partners.get(slug)
    if not p or (public and not p["enabled"]):
        raise HTTPException(404, "No encontrado")
    return p


def _menu_or_503(p: Dict[str, Any]) -> Dict[str, Any]:
    m = menu_mod.load(p["id"])
    if not m:
        raise HTTPException(503, "El menú todavía no está disponible")
    return m


@app.exception_handler(orders.OrderError)
async def _order_error(request: Request, exc: orders.OrderError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


# ---------------------------------------------------------------------------------------------- salud
@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


# ---------------------------------------------------------------------------------------------- tienda
def _store_ctx(p: Dict[str, Any]) -> Dict[str, Any]:
    theme = themes.merge(p["theme"])
    return {"p": p, "theme": theme, "css_vars": themes.css_vars(theme), "fonts_href": themes.fonts_href(theme),
            "profile": p["profile"] or {}, "currency": settings.currency,
            "yandex_key": settings.yandex_maps_api_key,
            "store_json": script_json({"slug": p["slug"], "name": p["name"], "currency": settings.currency,
                                      "center": [p.get("place_lat") or -17.7833, p.get("place_lon") or -63.1821],
                                      "accepting": bool(p["accepting_orders"]),
                                      "yandexKey": settings.yandex_maps_api_key,
                                      "phonePrefix": settings.phone_prefix})}


@app.get("/{slug}/pedido/{token}", response_class=HTMLResponse)
async def order_page(request: Request, slug: str, token: str):
    p = _partner_or_404(slug)
    o = orders.get(token)
    if not o or o["partner_id"] != p["id"]:
        raise HTTPException(404, "Pedido no encontrado")
    ctx = _store_ctx(p)
    ctx["order_json"] = script_json(orders.public_view(o))
    ctx["token"] = token
    return templates.TemplateResponse(request, "order.html", ctx)


@app.get("/api/{slug}/menu")
async def api_menu(slug: str):
    p = _partner_or_404(slug)
    return menu_mod.public_menu(_menu_or_503(p))


@app.post("/api/{slug}/geocode")
async def api_geocode(slug: str, body: Dict[str, Any] = Body(...)):
    p = _partner_or_404(slug)
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Coordenadas inválidas")
    if not geo.in_bounds(lat, lon):
        return {"ok": False, "detail": "Fuera de la zona de servicio"}
    try:
        res = geo.parse_finalsuggest(await rt().source.reverse_geocode(p["account"], lat, lon))
    except Exception as e:
        log.warning("geocode %s: %s", slug, e)
        res = None
    return {"ok": bool(res), "address": res}


@app.post("/api/{slug}/quote")
async def api_quote(slug: str, body: Dict[str, Any] = Body(...)):
    p = _partner_or_404(slug)
    if not p.get("place_slug"):
        raise HTTPException(503, "Restaurante en configuración")
    try:
        lat, lon = float(body["lat"]), float(body["lon"])
        subtotal = max(0.0, float(body.get("subtotal") or 0))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "Coordenadas inválidas")
    if not geo.in_bounds(lat, lon):
        return {"available": False, "reason": "fuera_de_zona", "can_order": False}
    try:
        q = await rt().quotes.quote(p["account"], p["place_slug"], lat, lon)
    except Exception as e:
        log.warning("quote %s: %s", slug, e)
        raise HTTPException(503, "No pudimos cotizar el envío, intenta de nuevo")
    out = quotes.price_for(q, subtotal)
    out["min_order"] = max(out.get("min_order") or 0, p.get("min_order") or 0)
    out["below_min_order"] = subtotal < out["min_order"]
    out["can_order"] = out["can_order"] and not out["below_min_order"] and bool(p["accepting_orders"])
    out.pop("surge_info", None)
    return out


_ip_hits: Dict[str, Deque[float]] = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    now, dq = time.time(), _ip_hits[ip]
    while dq and now - dq[0] > settings.orders_per_ip_window:
        dq.popleft()
    if len(dq) >= settings.orders_per_ip:
        return True
    dq.append(now)
    return False


@app.post("/api/{slug}/orders")
async def api_create_order(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    p = _partner_or_404(slug)
    if not p["accepting_orders"]:
        raise orders.OrderError("El restaurante no está recibiendo pedidos ahora")
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    m = _menu_or_503(p)
    priced = orders.prepare(p, m, body)
    if _rate_limited(ip):
        raise HTTPException(429, "Demasiados pedidos seguidos, espera unos minutos")
    a = priced["address"]
    try:
        q = await rt().quotes.quote(p["account"], p["place_slug"], a["lat"], a["lon"])
    except Exception:
        raise HTTPException(503, "No pudimos cotizar el envío, intenta de nuevo")
    summary = orders.check_quote(priced, q, p)
    expected = body.get("expected_total")
    if expected is not None and abs(float(expected) - summary["total"]) > 0.01:
        raise orders.OrderError(f"El total cambió a {settings.currency}{summary['total']:g}. Revisa y confirma de nuevo.")
    o = orders.create(p, priced, summary, client_ip=ip)
    rt().dispatcher.enqueue(o["account"], o["token"])
    return {"token": o["token"], "number": o["number"], "url": f"/{slug}/pedido/{o['token']}",
            "order": orders.public_view(o)}


@app.get("/api/{slug}/orders/{token}")
async def api_order(slug: str, token: str):
    p = _partner_or_404(slug)
    o = orders.get(token)
    if not o or o["partner_id"] != p["id"]:
        raise HTTPException(404, "Pedido no encontrado")
    return orders.public_view(o)


# ---------------------------------------------------------------------------------------------- portal
@app.get("/{slug}/portal", response_class=HTMLResponse)
async def portal_page(request: Request, slug: str):
    p = _partner_or_404(slug, public=False)
    data = auth.verify(request.cookies.get(auth.PORTAL_COOKIE))
    logged = auth.is_admin(request) or bool(data and data.get("slug") == slug)
    return templates.TemplateResponse(request, "portal.html", {
        "p": p, "logged": logged, "fonts": list(themes.FONTS), "choices": themes.CHOICES,
        "defaults": themes.DEFAULTS, "currency": settings.currency})


@app.post("/api/{slug}/portal/login")
async def portal_login(slug: str, response: Response, body: Dict[str, Any] = Body(...)):
    p = _partner_or_404(slug, public=False)
    if not partners.check_password(str(body.get("password") or ""), p.get("portal_password_hash")):
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Contraseña incorrecta")
    response.set_cookie(auth.PORTAL_COOKIE, auth.portal_token(slug), max_age=auth.TTL, httponly=True,
                        samesite="lax", secure=settings.public_base_url.startswith("https"))
    return {"ok": True}


@app.post("/api/{slug}/portal/logout")
async def portal_logout(slug: str, response: Response):
    response.delete_cookie(auth.PORTAL_COOKIE)
    return {"ok": True}


@app.get("/api/{slug}/portal/me")
async def portal_me(request: Request, slug: str):
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    m = menu_mod.load(p["id"])
    return {"slug": p["slug"], "name": p["name"], "theme": themes.merge(p["theme"]), "profile": p["profile"],
            "accepting_orders": bool(p["accepting_orders"]), "enabled": bool(p["enabled"]),
            "menu_items": len(m["items"]) if m else 0, "menu_synced_at": m.get("synced_at") if m else None,
            "public_url": f"{settings.public_base_url}/{p['slug']}"}


@app.put("/api/{slug}/portal/theme")
async def portal_theme(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    auth.require_portal(request, slug)
    _partner_or_404(slug, public=False)
    try:
        return partners.set_theme(slug, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))


@app.put("/api/{slug}/portal/profile")
async def portal_profile(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    auth.require_portal(request, slug)
    _partner_or_404(slug, public=False)
    return partners.set_profile(slug, body)


@app.put("/api/{slug}/portal/accepting")
async def portal_accepting(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    auth.require_portal(request, slug)
    _partner_or_404(slug, public=False)
    partners.update(slug, accepting_orders=1 if body.get("accepting") else 0)
    return {"accepting_orders": bool(body.get("accepting"))}


ALLOWED_IMAGES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@app.post("/api/{slug}/portal/upload")
async def portal_upload(request: Request, slug: str, file: UploadFile = File(...)):
    auth.require_portal(request, slug)
    _partner_or_404(slug, public=False)
    ext = ALLOWED_IMAGES.get(file.content_type or "")
    if not ext:
        raise HTTPException(422, "Sube una imagen PNG, JPG o WEBP")
    data = await file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(422, "La imagen supera 5 MB")
    name = f"{slug}-{secrets.token_hex(6)}.{ext}"
    (settings.uploads_dir / name).write_bytes(data)
    return {"url": f"/uploads/{name}"}


@app.get("/api/{slug}/portal/orders")
async def portal_orders(request: Request, slug: str):
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    out = []
    for o in orders.list_orders(partner_id=p["id"], limit=200):
        v = orders.public_view(o)
        v.update({"customer": f"{o['customer']['first_name']} {o['customer']['last_name']}",
                  "phone": o["customer"]["phone"], "yango_order_nr": o["yango_order_nr"]})
        out.append(v)
    return out


# ---------------------------------------------------------------------------------------------- admin
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request, "admin.html", {"logged": auth.is_admin(request),
                                                              "currency": settings.currency})


@app.post("/api/admin/login")
async def admin_login(response: Response, body: Dict[str, Any] = Body(...)):
    if not hmac.compare_digest(str(body.get("key") or ""), settings.admin_key):
        await asyncio.sleep(0.5)
        raise HTTPException(401, "Clave incorrecta")
    response.set_cookie(auth.ADMIN_COOKIE, auth.admin_token(), max_age=auth.TTL, httponly=True, samesite="lax",
                        secure=settings.public_base_url.startswith("https"))
    return {"ok": True}


@app.get("/api/admin/health")
async def admin_health(request: Request):
    auth.require_admin(request)
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM orders GROUP BY status")}
    return {"source": rt().source.health(), "queues": rt().dispatcher.health(), "quotes": rt().quotes.stats,
            "orders": counts, "order_placement_enabled": settings.order_placement_enabled}


@app.get("/api/admin/partners")
async def admin_partners(request: Request):
    auth.require_admin(request)
    out = []
    for p in partners.all_partners():
        m = db.one("SELECT synced_at, error, length(data) n FROM menus WHERE partner_id=?", (p["id"],))
        p.pop("portal_password_hash", None)
        p["menu"] = dict(m) if m else None
        out.append(p)
    return out


@app.post("/api/admin/partners")
async def admin_create_partner(request: Request, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    try:
        p = partners.create(body.get("slug", ""), body.get("name", ""), body.get("account", ""),
                            yango_url=body.get("yango_url", ""), place_slug=body.get("place_slug", ""),
                            password=body.get("password") or None)
    except ValueError as e:
        raise HTTPException(422, str(e))
    p.pop("portal_password_hash", None)
    return p


@app.put("/api/admin/partners/{slug}")
async def admin_update_partner(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    _partner_or_404(slug, public=False)
    p = partners.update(slug, **body)
    p.pop("portal_password_hash", None)
    return p


@app.post("/api/admin/partners/{slug}/password")
async def admin_partner_password(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    _partner_or_404(slug, public=False)
    try:
        partners.set_password(slug, str(body.get("password") or ""))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.post("/api/admin/partners/{slug}/sync")
async def admin_sync(request: Request, slug: str):
    auth.require_admin(request)
    p = _partner_or_404(slug, public=False)
    try:
        return await rt().sync_partner(p)
    except Exception as e:
        raise HTTPException(502, f"No se pudo sincronizar: {e}")


@app.get("/api/admin/accounts")
async def admin_accounts(request: Request):
    auth.require_admin(request)
    rows = [dict(r) for r in db.query("SELECT * FROM accounts ORDER BY name")]
    for r in rows:
        r["has_session"] = sessions.has_session(r["name"])
        r["partners"] = [p["slug"] for p in partners.all_partners() if p["account"] == r["name"]]
    return rows


@app.post("/api/admin/accounts/{name}/cookies")
async def admin_import_cookies(request: Request, name: str):
    auth.require_admin(request)
    raw = await request.body()
    try:
        res = sessions.import_session(name, raw.decode("utf-8", "replace"))
    except sessions.SessionImportError as e:
        raise HTTPException(422, str(e))
    return res


@app.post("/api/admin/accounts/{name}/check")
async def admin_check_account(request: Request, name: str):
    auth.require_admin(request)
    res = await rt().source.check_session(name)
    sessions.set_status(name, "ok" if res["ok"] else "caida", res["detail"])
    return res


@app.get("/api/admin/orders")
async def admin_orders(request: Request, status: Optional[str] = None, partner: Optional[str] = None):
    auth.require_admin(request)
    pid = partners.get(partner)["id"] if partner and partners.get(partner) else None
    names = {p["id"]: p["name"] for p in partners.all_partners()}
    out = orders.list_orders(partner_id=pid, status=status, limit=300)
    for o in out:
        o["partner_name"] = names.get(o["partner_id"])
        o.pop("client_ip", None)
    return out


@app.post("/api/admin/orders/{token}/status")
async def admin_order_status(request: Request, token: str, body: Dict[str, Any] = Body(...)):
    """Operador: marcar colocado a mano (con el N° de Yango), cancelar o reintentar con el bot."""
    auth.require_admin(request)
    o = orders.get(token)
    if not o:
        raise HTTPException(404, "Pedido no encontrado")
    action = body.get("action")
    if action == "colocado":
        nr = str(body.get("yango_order_nr") or "").strip()
        if not nr:
            raise HTTPException(422, "Falta el número de pedido de Yango")
        return orders.set_status(token, "colocado", yango_order_nr=nr[:40], error=None)
    if action == "cancelar":
        return orders.set_status(token, "cancelado")
    if action == "reintentar":
        if o["status"] not in ("manual", "revision"):
            raise HTTPException(422, "Solo se reintentan pedidos en manual o revisión")
        o = orders.set_status(token, "en_cola", error=None)
        rt().dispatcher.enqueue(o["account"], token)
        return o
    raise HTTPException(422, "Acción inválida")


@app.get("/api/admin/events")
async def admin_events(request: Request, order: Optional[str] = None, limit: int = 200):
    auth.require_admin(request)
    if order:
        rows = db.query("SELECT * FROM events WHERE order_token=? ORDER BY id DESC LIMIT ?", (order, limit))
    else:
        rows = db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (min(limit, 1000),))
    return [dict(r) for r in rows]


# Al final: la tienda captura /<slug>.
@app.get("/{slug}", response_class=HTMLResponse)
async def store_page(request: Request, slug: str):
    if slug in partners.RESERVED:
        raise HTTPException(404)
    p = _partner_or_404(slug)
    return templates.TemplateResponse(request, "store.html", _store_ctx(p))


@app.get("/{slug}/", include_in_schema=False)
async def store_slash(slug: str):
    return RedirectResponse(f"/{slug}", status_code=308)
