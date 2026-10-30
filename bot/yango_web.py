"""food.yango.com real con Playwright.

Cada cuenta cliente (una por restaurante) tiene su contexto de navegador con su sesión y un pool de pestañas
abiertas en food.yango.com. Las consultas (cotización, menú, geocodificación) se hacen con `fetch()` desde esas
pestañas, igual que la propia web: sin estado, en paralelo y en ~0,3–0,7 s. El carrito es uno por cuenta, así
que los pedidos de una cuenta se colocan de a uno (lock por cuenta).
"""
import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from app.config import ROOT, settings
from bot import sessions
from bot.base import PlacementMismatch, SessionDown, YangoSource

log = logging.getLogger("yango_web")
CONFIG_PATH = ROOT / "config" / "yango_web.yaml"

FETCH_JS = """
async ([method, path, body]) => {
  const opts = {method, headers: {'accept': 'application/json'}, credentials: 'include'};
  if (body !== null) { opts.body = JSON.stringify(body); opts.headers['content-type'] = 'application/json'; }
  const r = await fetch(path, opts);
  return {status: r.status, text: await r.text()};
}
"""


def load_config() -> Dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text())


def dig(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def fill_template(value: Any, ctx: Dict[str, Any]) -> Any:
    """Reemplaza {placeholders}. Un string que es exactamente "{x}" toma el valor tal cual (lista, número)."""
    if isinstance(value, dict):
        return {k: fill_template(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_template(v, ctx) for v in value]
    if isinstance(value, str):
        m = re.fullmatch(r"\{(\w+)\}", value)
        if m and m.group(1) in ctx:
            return ctx[m.group(1)]
        return re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m.group(1), m.group(0))), value)
    return value


def item_options_payload(line: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[int]] = {}
    for o in line["options"]:
        groups.setdefault(o["group_id"], []).append(int(o["option_id"]))
    return [{"group_id": int(g), "group_options": ids} for g, ids in groups.items()]


class AccountRuntime:
    def __init__(self, source: "YangoWebSource", account: str) -> None:
        self.source = source
        self.account = account
        self.context = None
        self.pages: List[Any] = []
        self._rr = 0
        self.sem = asyncio.Semaphore(settings.max_inflight_per_account)
        self.order_lock = asyncio.Lock()
        self._open_lock = asyncio.Lock()
        self.session_mtime = 0.0
        self.requests = 0
        self.errors = 0

    async def ensure(self) -> None:
        path = sessions.session_path(self.account)
        if not path.exists() and not settings.allow_anonymous:
            raise SessionDown(f"La cuenta {self.account} no tiene sesión cargada")
        mtime = path.stat().st_mtime if path.exists() else -1.0
        async with self._open_lock:
            if self.context is not None and mtime == self.session_mtime:
                return
            await self.close()
            self.context = await self.source.browser.new_context(
                storage_state=str(path) if path.exists() else None, locale="es-BO",
                timezone_id=settings.timezone, viewport={"width": 1280, "height": 900})
            self.session_mtime = mtime
            home = self.source.url(self.source.cfg["home_path"])
            for _ in range(max(1, settings.pages_per_account)):
                page = await self.context.new_page()
                await page.goto(home, wait_until="domcontentloaded", timeout=60000)
                self.pages.append(page)

    async def close(self) -> None:
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:
                pass
        self.context, self.pages = None, []

    def _page(self) -> Any:
        self._rr = (self._rr + 1) % len(self.pages)
        return self.pages[self._rr]

    async def fetch(self, method: str, path: str, body: Any = None) -> Any:
        await self.ensure()
        async with self.sem:
            page = self._page()
            self.requests += 1
            try:
                res = await asyncio.wait_for(page.evaluate(FETCH_JS, [method, path, body]),
                                             timeout=settings.bot_request_timeout)
            except Exception:
                self.errors += 1
                # La pestaña pudo navegar o cerrarse: se reabre la próxima vez.
                await self.close()
                raise
        status, text = res["status"], res["text"]
        if status in (401, 403):
            raise SessionDown(f"{self.account}: HTTP {status} en {path.split('?')[0]}")
        if status >= 400:
            raise RuntimeError(f"HTTP {status} en {path.split('?')[0]}: {text[:200]}")
        try:
            return json.loads(text) if text else {}
        except ValueError:
            raise RuntimeError(f"Respuesta no JSON en {path.split('?')[0]}")


class YangoWebSource(YangoSource):
    name = "yango_web"

    def __init__(self) -> None:
        self.cfg = load_config()
        self._pw = None
        self.browser = None
        self.accounts: Dict[str, AccountRuntime] = {}

    def url(self, path: str) -> str:
        return settings.yango_base_url + path.replace("{locale}", settings.yango_locale_path)

    async def start(self) -> None:
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        self.browser = await self._pw.chromium.launch(headless=settings.headless,
                                                      executable_path=settings.chromium_executable or None)

    async def stop(self) -> None:
        for rt in self.accounts.values():
            await rt.close()
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    def rt(self, account: str) -> AccountRuntime:
        if account not in self.accounts:
            self.accounts[account] = AccountRuntime(self, account)
        return self.accounts[account]

    async def _screenshot(self, page: Any, tag: str) -> Optional[str]:
        try:
            settings.debug_dir.mkdir(parents=True, exist_ok=True)
            p = settings.debug_dir / f"{int(time.time())}_{tag}.png"
            await page.screenshot(path=str(p), full_page=True)
            return p.name
        except Exception:
            return None

    # ------------------------------------------------------------------ consultas
    async def catalog(self, account: str, place_slug: str, lat: float, lon: float) -> Dict[str, Any]:
        path = self.cfg["endpoints"]["catalog"].format(place_slug=place_slug, lat=lat, lon=lon)
        return await self.rt(account).fetch("GET", path)

    async def menu(self, account: str, place_slug: str, lat: float, lon: float) -> Dict[str, Any]:
        path = self.cfg["endpoints"]["menu"].format(place_slug=place_slug, lat=lat, lon=lon)
        return await self.rt(account).fetch("GET", path)

    async def reverse_geocode(self, account: str, lat: float, lon: float) -> Dict[str, Any]:
        body = {"action": "pin_drop", "state": {"current_mode": "eats"}, "position": [lon, lat],
                "sticky": False, "type": "a"}
        return await self.rt(account).fetch("POST", self.cfg["endpoints"]["finalsuggest"], body)

    async def search_address(self, account: str, text: str, lat: float, lon: float) -> Dict[str, Any]:
        body = {"part": text, "position": [lon, lat], "state": {"current_mode": "eats"}, "type": "a",
                "action": "user_input"}
        return await self.rt(account).fetch("POST", self.cfg["endpoints"]["suggest"], body)

    async def resolve_place(self, account: str, yango_url: str) -> Dict[str, Any]:
        rt = self.rt(account)
        await rt.ensure()
        page = await rt.context.new_page()
        found: Dict[str, Any] = {}
        regex = re.compile(self.cfg["endpoints"]["brand_place_regex"])

        async def on_response(resp: Any) -> None:
            if regex.search(resp.url) and not found:
                try:
                    found.update(await resp.json())
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            await page.goto(yango_url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(40):
                if found:
                    break
                await asyncio.sleep(0.5)
            if not found:
                shot = await self._screenshot(page, f"resolve_{account}")
                raise RuntimeError(f"No se encontró el local en {yango_url} (captura {shot})")
        finally:
            await page.close()
        place = ((found.get("payload") or {}).get("foundPlace") or {}).get("place") or {}
        loc = (place.get("address") or {}).get("location") or {}
        logo = None
        for th in place.get("logo") or []:
            for v in th.get("value") or []:
                logo = logo or v.get("logo_url")
        accent = next((a.get("value") for a in place.get("accent_color") or [] if a.get("theme") == "light"), None)
        return {"place_slug": place.get("slug"), "place_id": place.get("id"), "name": place.get("name"),
                "lat": loc.get("latitude"), "lon": loc.get("longitude"),
                "address": (place.get("address") or {}).get("short"), "logo_url": logo, "accent_color": accent}

    async def check_session(self, account: str) -> Dict[str, Any]:
        rt = self.rt(account)
        try:
            await rt.ensure()
        except SessionDown as e:
            return {"ok": False, "detail": str(e)}
        page = rt.pages[0]
        try:
            await page.reload(wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)
            body = await page.inner_text("body")
        except Exception as e:
            await rt.close()
            return {"ok": False, "detail": f"no carga food.yango.com: {e}"}
        if any(t.lower() in body.lower() for t in self.cfg["session"]["captcha_texts"]):
            return {"ok": False, "detail": "Yango muestra captcha"}
        header = body[:400]
        if any(re.search(rf"\b{re.escape(t)}\b", header) for t in self.cfg["session"]["logged_out_texts"]):
            return {"ok": False, "detail": "sesión cerrada (aparece «Log in»)"}
        return {"ok": True, "detail": "sesión activa"}

    # ------------------------------------------------------------------ pedidos
    async def place_order(self, account: str, partner: Dict[str, Any], order: Dict[str, Any]) -> Dict[str, Any]:
        ocfg = self.cfg["order"]
        rt = self.rt(account)
        addr = order["address"]
        async with rt.order_lock:
            await rt.fetch(ocfg["clear_cart"]["method"], ocfg["clear_cart"]["path"])
            for line in order["items"]:
                ctx = {"item_id": int(line["item_id"]), "quantity": int(line["quantity"]),
                       "place_slug": partner["place_slug"], "item_options": item_options_payload(line)}
                await rt.fetch(ocfg["add_item"]["method"], ocfg["add_item"]["path"],
                               fill_template(ocfg["add_item"]["body"], ctx))
            cart = await rt.fetch("GET", self.cfg["endpoints"]["cart"].format(lat=addr["lat"], lon=addr["lon"]))
            y_sub = float(dig(cart, ocfg["cart_subtotal_path"]) or 0)
            y_fee = float(dig(cart, ocfg["cart_delivery_fee_path"]) or 0)
            y_total = float(dig(cart, ocfg["cart_total_path"]) or 0)
            if abs(y_sub - order["subtotal"]) > 0.01:
                raise PlacementMismatch(f"Subtotal en Yango {y_sub} ≠ web {order['subtotal']}")
            if y_fee - order["delivery_fee"] > settings.fee_tolerance:
                raise PlacementMismatch(f"Envío en Yango {y_fee} > cotizado {order['delivery_fee']}")
            nr = await self._checkout(rt, order, ocfg)
        return {"yango_order_nr": nr, "yango_total": y_total, "delivery_fee": y_fee}

    async def _checkout(self, rt: AccountRuntime, order: Dict[str, Any], ocfg: Dict[str, Any]) -> str:
        addr = order["address"]
        ctx = {"address_text": addr["text"], "reference": addr["reference"], "comment": order["yango_comment"],
               "lat": addr["lat"], "lon": addr["lon"], "locale": settings.yango_locale_path}
        never = [n.lower() for n in ocfg.get("never_click") or []]
        page = await rt.context.new_page()
        captured: Dict[str, Any] = {}
        responses: List[Any] = []
        page.on("response", lambda r: responses.append(r))
        try:
            for step in ocfg["checkout_steps"]:
                act, optional = step["action"], step.get("optional", False)
                try:
                    if act == "goto":
                        await page.goto(self.url(fill_template(step["path"], ctx)), wait_until="domcontentloaded",
                                        timeout=60000)
                    elif act == "click":
                        names = [fill_template(n, ctx) for n in step["names"]]
                        if any(n.lower() in never for n in names):
                            raise RuntimeError("paso de checkout apunta a un botón prohibido")
                        await self._click_any(page, names)
                    elif act == "fill":
                        await self._fill_any(page, step["labels"], fill_template(step["value"], ctx))
                    elif act == "press":
                        await page.keyboard.press(step["key"])
                    elif act == "wait_text":
                        await page.get_by_text(step["text"]).first.wait_for(timeout=step.get("timeout", 15000))
                    elif act == "capture":
                        # Respuestas que la web recibió tras pulsar «Hacer pedido» (se escuchan desde el inicio).
                        rx = re.compile(step["response_regex"])
                        deadline = time.monotonic() + step.get("timeout", 30)
                        seen = 0
                        while not captured.get("nr") and time.monotonic() < deadline:
                            for resp in responses[seen:]:
                                if rx.search(resp.url) and resp.request.method != "GET":
                                    try:
                                        data = await resp.json()
                                    except Exception:
                                        continue
                                    for p in step["json_paths"]:
                                        v = dig(data, p)
                                        if v:
                                            captured["nr"] = str(v)
                                            break
                            seen = len(responses)
                            await asyncio.sleep(0.3)
                except Exception as e:
                    if optional:
                        continue
                    shot = await self._screenshot(page, f"checkout_{rt.account}_{order['number']}")
                    raise RuntimeError(f"checkout, paso {act}: {e} (captura {shot})")
            if not captured.get("nr"):
                shot = await self._screenshot(page, f"checkout_sin_nr_{order['number']}")
                raise RuntimeError(f"No se pudo leer el número de pedido de Yango (captura {shot})")
            return captured["nr"]
        finally:
            await page.close()

    async def _click_any(self, page: Any, names: List[str]) -> None:
        for n in names:
            for loc in (page.get_by_role("button", name=n), page.get_by_text(n, exact=True)):
                if await loc.count():
                    await loc.first.click(timeout=8000)
                    await page.wait_for_timeout(600)
                    return
        raise RuntimeError(f"no aparece ninguno de {names}")

    async def _fill_any(self, page: Any, labels: List[str], value: str) -> None:
        for lab in labels:
            for loc in (page.get_by_label(lab), page.get_by_placeholder(lab)):
                if await loc.count():
                    await loc.first.fill(value, timeout=8000)
                    return
        raise RuntimeError(f"no aparece el campo {labels}")

    def health(self) -> Dict[str, Any]:
        return {"source": self.name, "order_flow_verified": bool(self.cfg["order"].get("verified")),
                "accounts": {a: {"open": rt.context is not None, "pages": len(rt.pages), "requests": rt.requests,
                                 "errors": rt.errors} for a, rt in self.accounts.items()}}
