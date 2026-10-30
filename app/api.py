"""API y páginas: tienda pública (dominio.com/<slug>), portal del partner y panel de admin."""
import asyncio
import hashlib
import hmac
import json
import logging
import re
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any, Deque, Dict, Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import (alerts, auth, billing, branches, dashboard, db, geo, menu as menu_mod, orders, partners, push, quotes, runtime as runtime_mod, themes,
                 tiktok, upsell)
from app.config import ROOT, settings
from bot import sessions
from bot.login import LoginError

log = logging.getLogger("api")
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))


def _asset_version() -> str:
    """Huella de app/static: va en las URLs (?v=) para que el navegador no use una versión vieja tras publicar."""
    h = hashlib.sha1()
    for f in sorted((ROOT / "app" / "static").rglob("*")):
        if f.is_file():
            h.update(f.name.encode()); h.update(f.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["v"] = _asset_version()


def seed_demo() -> None:
    if partners.all_partners():
        return
    partners.create("wrap-roll", "Wrap & Roll", "cuenta_demo",
                    yango_url="https://food.yango.com/bo/santa-cruz-de-la-sierra/r/wrap_roll_-_san_martin",
                    password=os.getenv("DEMO_PORTAL_PASSWORD", "demo12345"),
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


def _store_videos(p: Dict[str, Any]) -> list:
    """Videos para la tienda; los guardados antes con un solo producto (item_id) pasan a item_ids."""
    return [{**v, "item_ids": v.get("item_ids") or ([v["item_id"]] if v.get("item_id") else [])}
            for v in (p["profile"] or {}).get("tiktok") or []]


def _bestsellers(p: Dict[str, Any]) -> Dict[str, Any]:
    m = menu_mod.load(p["id"])
    if not m:
        return {"items": [], "source": ""}
    return upsell.bestsellers(p["id"], m, (p["profile"] or {}).get("bestsellers"))


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
    """Landing de carta.bo para vender el servicio a restaurantes."""
    demo = next((p["slug"] for p in partners.all_partners() if p["enabled"]), None)
    return templates.TemplateResponse(request, "landing.html", {
        "price": settings.plan_price_bs, "sponsored": settings.plan_sponsored_months,
        "min_paid": settings.plan_min_paid_months, "currency": settings.currency, "demo": demo,
        "whatsapp": re.sub(r"\D", "", settings.sales_whatsapp)})


_lead_hits: Dict[str, Deque[float]] = defaultdict(deque)


@app.post("/api/leads")
async def api_lead(request: Request, body: Dict[str, Any] = Body(...)):
    """Formulario de la landing: queda en Eventos (admin) y se avisa por Telegram."""
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    now, dq = time.time(), _lead_hits[ip]
    while dq and now - dq[0] > 3600:
        dq.popleft()
    if len(dq) >= 5:
        raise HTTPException(429, "Ya recibimos tus datos, te escribimos pronto")
    clean = lambda k, n: re.sub(r"[<>]", "", " ".join(str(body.get(k) or "").split()))[:n]  # noqa: E731
    lead = {"nombre": clean("name", 80), "restaurante": clean("restaurant", 80), "telefono": clean("phone", 20),
            "sucursales": clean("branches", 4), "mensaje": clean("message", 400)}
    if len(lead["nombre"]) < 2 or len(lead["restaurante"]) < 2 or len(re.sub(r"\D", "", lead["telefono"])) < 7:
        raise HTTPException(422, "Escribe tu nombre, tu restaurante y un celular de contacto")
    dq.append(now)
    db.event("lead", lead)
    await alerts.alert("lead", f"Nuevo interesado en carta.bo: {lead['restaurante']} · {lead['nombre']} · "
                               f"{lead['telefono']} · {lead['sucursales'] or '?'} sucursal(es). {lead['mensaje']}")
    return {"ok": True}


# ---------------------------------------------------------------------------------------------- tienda
def _store_ctx(p: Dict[str, Any]) -> Dict[str, Any]:
    theme = themes.merge(p["theme"])
    return {"p": p, "theme": theme, "css_vars": themes.css_vars(theme), "fonts_href": themes.fonts_href(theme),
            "profile": p["profile"] or {}, "currency": settings.currency,
            "store_json": script_json({"slug": p["slug"], "name": p["name"], "currency": settings.currency,
                                      "center": [p.get("place_lat") or -17.7833, p.get("place_lon") or -63.1821],
                                      "accepting": bool(p["accepting_orders"]),
                                      "tiktok": _store_videos(p),
                                      "bestsellers": _bestsellers(p)["items"],
                                      "branch": {"name": p.get("place_name") or p["name"], "lat": p.get("place_lat"),
                                                 "lon": p.get("place_lon")} if p.get("place_lat") is not None else None,
                                      "yandexKey": settings.yandex_maps_api_key,
                                      "mapLang": settings.yandex_maps_lang,
                                      "phonePrefix": settings.phone_prefix, "v": templates.env.globals["v"]})}


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Service worker de la web del cliente (en la raíz para que cubra todas las tiendas): avisos push del pedido."""
    return Response((ROOT / "app" / "static" / "sw.js").read_text(), media_type="application/javascript",
                    headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@app.get("/{slug}/manifest.webmanifest", include_in_schema=False)
async def web_manifest(slug: str, order: Optional[str] = None):
    """Manifest para instalar la tienda como app. Con ?order= abre directo en el seguimiento de ese pedido."""
    p = _partner_or_404(slug)
    theme = themes.merge(p["theme"])
    start = f"/{slug}"
    if order and re.fullmatch(r"[A-Za-z0-9_-]{10,40}", order):
        o = orders.get(order)
        if o and o["partner_id"] == p["id"]:
            start = f"/{slug}/pedido/{order}"
    icons = [{"src": f"/{slug}/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]
    if theme.get("logo_url"):
        icons.insert(0, {"src": theme["logo_url"], "sizes": "512x512", "purpose": "any"})
    return JSONResponse({"name": p["name"], "short_name": p["name"][:24], "start_url": start, "scope": f"/{slug}",
                         "id": f"/{slug}", "display": "standalone", "background_color": theme.get("background") or "#ffffff",
                         "theme_color": theme.get("primary") or "#111111", "lang": "es", "icons": icons},
                        media_type="application/manifest+json")


@app.get("/{slug}/icon.svg", include_in_schema=False)
async def store_icon(slug: str):
    """Ícono de la app instalada: inicial del restaurante sobre su color (si no hay logo)."""
    p = _partner_or_404(slug)
    theme = themes.merge(p["theme"])
    color = theme.get("primary") or "#111111"
    letter = (p["name"][:1] or "C").upper().replace("&", "&amp;").replace("<", "&lt;")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" rx="112" '
           f'fill="{color}"/><text x="256" y="340" text-anchor="middle" font-family="Inter,Arial,sans-serif" '
           f'font-size="260" font-weight="700" fill="{theme.get("on_primary") or "#fff"}">{letter}</text></svg>')
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/push/key")
async def push_key():
    return {"key": push.keys()["public"]}


@app.post("/api/{slug}/orders/{token}/push")
async def api_order_push(slug: str, token: str, body: Dict[str, Any] = Body(...)):
    """El cliente activa los avisos de su pedido: guarda la suscripción Web Push del teléfono."""
    p = _partner_or_404(slug)
    o = orders.get(token)
    if not o or o["partner_id"] != p["id"]:
        raise HTTPException(404, "Pedido no encontrado")
    try:
        push.subscribe(token, body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"ok": True, "subscriptions": push.count(token)}


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


def _branch_or_404(p: Dict[str, Any], slug: Optional[str]) -> Dict[str, Any]:
    b = branches.get(p, slug)
    if not b:
        raise HTTPException(404, "Sucursal desconocida")
    return b


async def _branch_menu(p: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    if b["primary"]:
        return _menu_or_503(p)
    try:
        m = await branches.menu(rt().source, p, b)
    except Exception as e:
        log.warning("menú de la sucursal %s: %s", b["slug"], e)
        m = None
    if not m:
        raise HTTPException(503, "El menú de esta sucursal no está disponible ahora")
    return m


@app.get("/api/{slug}/menu")
async def api_menu(slug: str, branch: Optional[str] = None):
    """Menú de la sucursal (por defecto la principal). `alias`: ids de la principal → ids de esta sucursal,
    para traducir lo configurado una vez (más vendidos, videos de TikTok)."""
    p = _partner_or_404(slug)
    b = _branch_or_404(p, branch)
    m = await _branch_menu(p, b)
    out = menu_mod.public_menu(m)
    out["branch"] = b
    if not b["primary"]:
        main = menu_mod.load(p["id"])
        out["alias"] = branches.alias(main, m) if main else {}
    return out


@app.get("/api/{slug}/branch")
async def api_branch(slug: str, lat: float, lon: float):
    """Sucursal de la marca que atiende ese punto (la que Yango asigna). branch=None: ninguna llega."""
    p = _partner_or_404(slug)
    if not geo.in_bounds(lat, lon):
        return {"branch": None}
    try:
        return {"branch": await branches.find(rt().source, p, lat, lon)}
    except Exception as e:
        log.warning("sucursal %s: %s", slug, e)
        raise HTTPException(503, "No pudimos consultar la sucursal, intenta de nuevo")


@app.get("/api/{slug}/upsell")
async def api_upsell(slug: str, items: str = "", context: str = "item", branch: Optional[str] = None):
    """Sugerencias para la ficha de un producto (context=item) o para cerrar el carrito (context=cart)."""
    p = _partner_or_404(slug)
    m = await _branch_menu(p, _branch_or_404(p, branch))
    ids = [i for i in items.split(",") if i][:30]
    return {"items": upsell.suggest(p["id"], m, ids, "cart" if context == "cart" else "item")}


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


@app.post("/api/{slug}/address-search")
async def api_address_search(slug: str, body: Dict[str, Any] = Body(...)):
    """Lo que el cliente escribe en «Dirección» → direcciones cercanas (buscador de Yango) para mover el mapa."""
    p = _partner_or_404(slug)
    q = re.sub(r"\s+", " ", str(body.get("q") or "")).strip()[:80]
    if len(q) < 3:
        return {"results": []}
    lat, lon = p.get("place_lat") or -17.7833, p.get("place_lon") or -63.1821
    try:
        raw = await rt().source.search_address(p["account"], q, lat, lon)
    except Exception as e:
        log.warning("búsqueda de dirección %s: %s", slug, e)
        raise HTTPException(503, "No pudimos buscar la dirección, mueve el mapa hasta tu puerta")
    return {"results": geo.parse_suggest(raw, lat, lon)}


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
    b = _branch_or_404(p, body.get("branch"))
    try:
        q = await rt().quotes.quote(p["account"], b["slug"], lat, lon)
    except Exception as e:
        log.warning("quote %s: %s", slug, e)
        raise HTTPException(503, "No pudimos cotizar el envío, intenta de nuevo")
    out = quotes.price_for(q, subtotal)
    if out["reason"] == "cerrado" and b.get("lat") is not None:
        # Yango marca «no disponible por horario» tanto si el local cerró como si cerró la zona por demanda.
        # El estado real del local es el de su propia ubicación: si ahí atiende, lo que está cerrado es la zona.
        try:
            own = await rt().quotes.quote(p["account"], b["slug"], b["lat"], b["lon"])
            if own["available"]:
                out["reason"] = "zona_cerrada"
            else:
                out["opens_at"] = own.get("available_from")
        except Exception as e:
            log.warning("estado de la sucursal %s: %s", slug, e)
    out["min_order"] = max(out.get("min_order") or 0, p.get("min_order") or 0)
    out["below_min_order"] = subtotal < out["min_order"]
    out["can_order"] = out["can_order"] and not out["below_min_order"] and bool(p["accepting_orders"])
    out.pop("surge_info", None)
    out["branch"] = b["slug"]
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
    # La sucursal la decide el servidor con la dirección (como Yango), no el navegador.
    try:
        lat, lon = float((body.get("address") or {})["lat"]), float((body.get("address") or {})["lon"])
    except (KeyError, TypeError, ValueError):
        raise orders.OrderError("Marca tu ubicación en el mapa")
    try:
        b = await branches.find(rt().source, p, lat, lon) if geo.in_bounds(lat, lon) else None
    except Exception:
        raise HTTPException(503, "No pudimos confirmar la sucursal, intenta de nuevo")
    if not b:
        raise orders.OrderError("Por ahora no llegamos a esa dirección")
    if body.get("branch") and body["branch"] != b["slug"]:
        raise orders.OrderError(f"Esa dirección la atiende {b['name']}. Vuelve a elegir tu ubicación para ver su menú.")
    m = await _branch_menu(p, b)
    priced = orders.prepare(p, m, body)
    if _rate_limited(ip):
        raise HTTPException(429, "Demasiados pedidos seguidos, espera unos minutos")
    a = priced["address"]
    try:
        q = await rt().quotes.quote(p["account"], b["slug"], a["lat"], a["lon"])
    except Exception:
        raise HTTPException(503, "No pudimos cotizar el envío, intenta de nuevo")
    summary = orders.check_quote(priced, q, p)
    expected = body.get("expected_total")
    if expected is not None and abs(float(expected) - summary["total"]) > 0.01:
        raise orders.OrderError(f"El total cambió a {settings.currency}{summary['total']:g}. Revisa y confirma de nuevo.")
    o = orders.create(p, priced, summary, client_ip=ip)
    upsell.clear_cache(p["id"])
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


@app.get("/api/{slug}/portal/summary")
async def portal_summary(request: Request, slug: str, days: int = 30):
    """Ventas del restaurante para el inicio del portal (mismos números que el resumen del admin)."""
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    return dashboard.summary(max(0, min(days, 3650)), p["id"])


@app.get("/api/{slug}/portal/billing")
async def portal_billing(request: Request, slug: str):
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    return {**billing.summary(p), "payment_info": settings.plan_payment_info}


@app.post("/api/{slug}/portal/billing/accept")
async def portal_billing_accept(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    """El partner acepta el acuerdo (nombre de quien acepta, fecha, IP y versión de los términos)."""
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    if body.get("accept") is not True:
        raise HTTPException(422, "Marca la casilla para aceptar el acuerdo")
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")
    try:
        return {**billing.accept(p, str(body.get("name") or ""), ip), "payment_info": settings.plan_payment_info}
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.put("/api/{slug}/portal/accepting")
async def portal_accepting(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    auth.require_portal(request, slug)
    _partner_or_404(slug, public=False)
    partners.update(slug, accepting_orders=1 if body.get("accepting") else 0)
    return {"accepting_orders": bool(body.get("accepting"))}


@app.exception_handler(tiktok.TikTokError)
async def _tiktok_error(request: Request, exc: tiktok.TikTokError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


def _videos(p: Dict[str, Any]) -> list:
    return list((p["profile"] or {}).get("tiktok") or [])


@app.post("/api/{slug}/portal/tiktok")
async def portal_tiktok_add(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    """Agrega un video de TikTok (enlace) etiquetado con el producto que aparece."""
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    videos = _videos(p)
    if len(videos) >= tiktok.MAX_VIDEOS:
        raise HTTPException(422, f"Máximo {tiktok.MAX_VIDEOS} videos")
    v = await tiktok.resolve(str(body.get("url") or ""), slug)
    v["item_ids"] = _valid_items(p, body.get("item_ids") or ([body["item_id"]] if body.get("item_id") else []))
    videos = [x for x in videos if x["id"] != v["id"]] + [v]
    return partners.set_tiktok(slug, videos)


@app.put("/api/{slug}/portal/tiktok")
async def portal_tiktok_update(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    """Reordenar y cambiar los productos etiquetados: body {videos: [{id, item_ids}]} en el orden nuevo."""
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    current = {v["id"]: v for v in _videos(p)}
    out = []
    for x in body.get("videos") or []:
        v = current.get(str(x.get("id")))
        if v:
            ids = x.get("item_ids") if "item_ids" in x else ([x["item_id"]] if x.get("item_id") else [])
            out.append({**v, "item_ids": _valid_items(p, ids)})
    return partners.set_tiktok(slug, out)


def _valid_items(p: Dict[str, Any], ids: Any) -> list:
    m = menu_mod.load(p["id"])
    if not m or not isinstance(ids, list):
        return []
    return [str(i) for i in ids if str(i) in m["items"]]


@app.get("/api/{slug}/portal/bestsellers")
async def portal_bestsellers(request: Request, slug: str):
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    m = menu_mod.load(p["id"])
    cfg = (p["profile"] or {}).get("bestsellers") or {"mode": "auto", "items": []}
    auto = upsell.bestsellers(p["id"], m, None) if m else {"items": [], "source": ""}
    return {**cfg, "auto": auto, "current": _bestsellers(p)}


@app.put("/api/{slug}/portal/bestsellers")
async def portal_bestsellers_set(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    """«Más vendidos»: {mode: auto|manual, items: [ids en orden]}."""
    auth.require_portal(request, slug)
    p = _partner_or_404(slug, public=False)
    partners.set_bestsellers(slug, str(body.get("mode") or "auto"), _valid_items(p, body.get("items") or []))
    return await portal_bestsellers(request, slug)


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


@app.get("/api/admin/dashboard")
async def admin_dashboard(request: Request, days: int = 30, partner: Optional[str] = None):
    auth.require_admin(request)
    p = partners.get(partner) if partner else None
    out = dashboard.summary(max(0, min(days, 3650)), p["id"] if p else None)
    accounts = [dict(r) for r in db.query("SELECT name, status, detail, checked_at FROM accounts ORDER BY name")]
    used = {x["account"] for x in partners.all_partners() if x["enabled"]}
    out["accounts"] = [a for a in accounts if a["name"] in used] or accounts
    out["pairs"] = []
    for x in ([p] if p else partners.all_partners()):
        m = menu_mod.load(x["id"])
        if m:
            out["pairs"] += upsell.top_pairs(x["id"], m)
    out["pairs"] = sorted(out["pairs"], key=lambda r: -r["orders"])[:8]
    out["quotes"] = rt().quotes.stats
    out["source"] = rt().source.name
    out["order_placement_enabled"] = settings.order_placement_enabled
    return out


@app.get("/api/admin/partners")
async def admin_partners(request: Request):
    auth.require_admin(request)
    out = []
    for p in partners.all_partners():
        m = db.one("SELECT synced_at, error, length(data) n FROM menus WHERE partner_id=?", (p["id"],))
        p.pop("portal_password_hash", None)
        p["menu"] = dict(m) if m else None
        b = billing.summary(p)
        p["billing"] = {k: b[k] for k in ("state", "branches", "monthly_bs", "price_bs", "sponsored_months", "min_paid_months",
                                          "start_date", "sponsored_until", "commitment_until", "paid_until", "due_bs",
                                          "accepted_at", "accepted_name", "branches_contracted", "free_forever", "free_note")}
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
    try:
        p = partners.update(slug, **body)
    except ValueError as e:
        raise HTTPException(422, str(e))
    p.pop("portal_password_hash", None)
    return p


@app.put("/api/admin/partners/{slug}/billing")
async def admin_partner_billing(request: Request, slug: str, body: Dict[str, Any] = Body(...)):
    """Plan del partner: precio por sucursal, sucursales, meses que paga Yango, permanencia, inicio, pagado hasta."""
    auth.require_admin(request)
    p = _partner_or_404(slug, public=False)
    try:
        return billing.update(p, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, f"Dato inválido: {e}")


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
        r["partners"] = [p["slug"] for p in partners.all_partners() if r["name"] in (p["account"], p.get("vendor_account"))]
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


@app.exception_handler(LoginError)
async def _login_error(request: Request, exc: LoginError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.post("/api/admin/accounts/{name}/login")
async def admin_login_start(request: Request, name: str, body: Dict[str, Any] = Body(...)):
    """Login asistido con el teléfono de la cuenta: el bot pide el SMS, el operador escribe el código."""
    auth.require_admin(request)
    try:
        return await rt().login.start(name, str(body.get("phone") or ""))
    except sessions.SessionImportError as e:
        raise HTTPException(422, str(e))


@app.get("/api/admin/accounts/{name}/login")
async def admin_login_status(request: Request, name: str):
    auth.require_admin(request)
    return await rt().login.status(name)


@app.post("/api/admin/accounts/{name}/login/action")
async def admin_login_action(request: Request, name: str, body: Dict[str, Any] = Body(...)):
    auth.require_admin(request)
    return await rt().login.act(name, body)


@app.delete("/api/admin/accounts/{name}/login")
async def admin_login_cancel(request: Request, name: str):
    auth.require_admin(request)
    await rt().login.cancel(name)
    return {"ok": True}


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


@app.post("/api/admin/orders/{token}/dry-run")
async def admin_order_dry_run(request: Request, token: str):
    """Probar en Yango sin pedir: carrito, dirección, comentarios, NIT y Efectivo comprobado, sin el botón final.
    Usa el carrito de la cuenta: espera su turno en la cola de esa cuenta (un pedido a la vez)."""
    auth.require_admin(request)
    o = orders.get(token)
    if not o:
        raise HTTPException(404, "Pedido no encontrado")
    partner = partners.get_by_id(o["partner_id"])
    try:
        res = await rt().source.place_order(o["account"], partner, o, dry_run=True)
    except Exception as e:
        db.event("prueba_yango_fallo", {"error": str(e)[:300]}, order_token=token, partner_id=o["partner_id"])
        raise HTTPException(502, f"La prueba falló: {e}")
    db.event("prueba_yango_ok", res, order_token=token, partner_id=o["partner_id"])
    return res


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
