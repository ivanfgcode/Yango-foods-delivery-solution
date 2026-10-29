"""Inicio de sesión asistido en Yango ID con el teléfono de la cuenta (Admin → Cuentas → Iniciar sesión).

El bot abre la página de Yango ID, escribe el número y pide el SMS. Lo demás lo hace una persona desde el panel,
viendo una captura de la pantalla: escribe el código que llegó al teléfono y, si Yango muestra captcha, lo resuelve
ella (clic y texto sobre la captura). El bot no resuelve captchas. Cuando la web vuelve a food.yango.com con
sesión, se guarda el `storage_state` igual que si se hubieran cargado cookies a mano.
"""
import asyncio
import base64
import logging
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

from app import db
from app.config import settings
from bot import sessions

log = logging.getLogger("login")

VIEWPORT = {"width": 420, "height": 760}
FLOW_TTL = 600  # segundos: el SMS caduca antes
KEYS = {"Enter", "Backspace", "Tab", "Escape", "ArrowDown", "ArrowUp"}


class LoginError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """'7 123-4567', '+591 71234567' → '+59171234567' (celular de Bolivia)."""
    prefix = re.sub(r"\D", "", settings.phone_prefix)
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith(prefix) and len(digits) > 8:
        digits = digits[len(prefix):]
    if not re.fullmatch(r"[67]\d{7}", digits):
        raise LoginError("Escribe un celular válido (8 dígitos, empieza con 6 o 7)")
    return f"+{prefix}{digits}"


def mask_phone(phone: str) -> str:
    return phone[:-4] + "****" if len(phone) > 6 else "****"


class LoginFlow:
    def __init__(self, account: str, phone: str, context: Any, page: Any) -> None:
        self.account, self.phone = account, phone
        self.context, self.page = context, page
        self.started = time.monotonic()
        self.state = "iniciando"   # iniciando | esperando | listo | error
        self.detail = ""
        self.lock = asyncio.Lock()

    def expired(self) -> bool:
        return time.monotonic() - self.started > FLOW_TTL


class LoginManager:
    """Un intento de login por cuenta. Usa el navegador de la fuente real o abre uno propio (modo mock)."""

    def __init__(self, cfg: Dict[str, Any], browser: Any = None) -> None:
        self.cfg = cfg
        self.flows: Dict[str, LoginFlow] = {}
        self._browser = browser
        self._pw = None

    async def browser(self) -> Any:
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=settings.headless,
                                                           executable_path=settings.chromium_executable or None)
        return self._browser

    async def stop(self) -> None:
        for a in list(self.flows):
            await self.cancel(a)
        if self._pw:
            await self._browser.close()
            await self._pw.stop()

    def login_url(self) -> str:
        home = settings.yango_base_url + "/" + settings.yango_locale_path + "/"
        return self.cfg["url"].format(retpath=quote(home, safe=""))

    # ------------------------------------------------------------------ pasos
    async def start(self, account: str, raw_phone: str) -> Dict[str, Any]:
        sessions.session_path(account)  # valida el nombre
        phone = normalize_phone(raw_phone)
        await self.cancel(account)
        ctx = await (await self.browser()).new_context(locale="es-BO", timezone_id=settings.timezone,
                                                       viewport=VIEWPORT, is_mobile=True, has_touch=True)
        page = await ctx.new_page()
        flow = LoginFlow(account, phone, ctx, page)
        self.flows[account] = flow
        db.event("login_iniciado", {"telefono": mask_phone(phone)}, account=account)
        async with flow.lock:
            try:
                await page.goto(self.login_url(), wait_until="domcontentloaded", timeout=60000)
                field = page.locator(self.cfg["phone_input"]).first
                await field.wait_for(timeout=30000)
                # Yango pone el prefijo del país por IP (+1 en EE. UU.) un momento después de cargar: se espera,
                # se borra y se escribe con +591 (Yango cambia la bandera solo). Se verifica antes de continuar.
                try:
                    await page.wait_for_function("(s) => document.querySelector(s)?.value !== ''",
                                                 arg=self.cfg["phone_input"], timeout=8000)
                except Exception:
                    pass
                await self._accept_cookies(page)
                want = re.sub(r"\D", "", phone)
                for _ in range(3):
                    await field.click()
                    await page.keyboard.press("Control+A")
                    for _ in range(20):
                        await page.keyboard.press("Backspace")
                    await field.press_sequentially(phone, delay=60)
                    await page.wait_for_timeout(500)
                    if re.sub(r"\D", "", await field.input_value()) == want:
                        break
                else:
                    raise LoginError(f"Yango no aceptó el número (quedó «{await field.input_value()}»)")
                await self._accept_cookies(page)
                try:
                    await page.locator(self.cfg["phone_next"]).first.click(timeout=8000)
                except Exception:
                    await field.press("Enter")  # el formulario también se envía con Enter
                await page.wait_for_timeout(3000)
                flow.state, flow.detail = "esperando", "Escribe el código del SMS (o resuelve el captcha si aparece)"
            except Exception as e:
                flow.state, flow.detail = "error", f"No se pudo enviar el teléfono: {e}"
            return await self._snapshot(flow)

    async def act(self, account: str, action: Dict[str, Any]) -> Dict[str, Any]:
        flow = self._flow(account)
        async with flow.lock:
            if flow.state in ("listo", "error"):
                return await self._snapshot(flow)
            page, kind = flow.page, action.get("type")
            if kind == "click":
                x, y = float(action["x"]), float(action["y"])
                if not (0 <= x <= VIEWPORT["width"] and 0 <= y <= VIEWPORT["height"]):
                    raise LoginError("Clic fuera de la pantalla")
                await page.mouse.click(x, y)
            elif kind == "text":
                await page.keyboard.type(str(action.get("text") or "")[:80], delay=50)
            elif kind == "code":
                code = re.sub(r"\D", "", str(action.get("code") or ""))
                if not 4 <= len(code) <= 8:
                    raise LoginError("El código del SMS tiene entre 4 y 8 dígitos")
                await self._type_code(page, code)
            elif kind == "key":
                if action.get("key") not in KEYS:
                    raise LoginError("Tecla no permitida")
                await page.keyboard.press(action["key"])
            elif kind != "refresh":
                raise LoginError("Acción inválida")
            await page.wait_for_timeout(1800)
            return await self._snapshot(flow)

    async def status(self, account: str) -> Dict[str, Any]:
        flow = self._flow(account)
        async with flow.lock:
            return await self._snapshot(flow)

    async def cancel(self, account: str) -> None:
        flow = self.flows.pop(account, None)
        if flow is not None:
            try:
                await flow.context.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ util
    def _flow(self, account: str) -> LoginFlow:
        flow = self.flows.get(account)
        if flow is None:
            raise LoginError("No hay un inicio de sesión en curso para esta cuenta")
        if flow.expired() and flow.state != "listo":
            flow.state, flow.detail = "error", "Pasaron más de 10 minutos: vuelve a empezar"
        return flow

    async def _accept_cookies(self, page: Any) -> None:
        # El aviso de cookies tapa el botón «Continue»; sus botones no siempre tienen rol de botón.
        for sel in self.cfg.get("cookie_accept_selectors") or []:
            try:
                if await page.locator(sel).count() and await page.locator(sel).first.is_visible():
                    await page.locator(sel).first.click(timeout=3000)
                    await page.wait_for_timeout(400)
                    return
            except Exception:
                pass
        for name in self.cfg.get("cookie_accept") or []:
            for btn in (page.get_by_role("button", name=name, exact=True), page.get_by_text(name, exact=True)):
                try:
                    if await btn.count() and await btn.first.is_visible():
                        await btn.first.click(timeout=3000)
                        await page.wait_for_timeout(400)
                        return
                except Exception:
                    pass

    async def _type_code(self, page: Any, code: str) -> None:
        # Un campo único o una casilla por dígito: se enfoca el primero visible y se teclea, como una persona.
        for frame in page.frames:
            loc = frame.locator(self.cfg["code_input"])
            for i in range(await loc.count()):
                if await loc.nth(i).is_visible():
                    await loc.nth(i).click()
                    await page.keyboard.type(code, delay=90)
                    return
        await page.keyboard.type(code, delay=90)

    async def _logged_in(self, flow: LoginFlow) -> bool:
        host = urlparse(flow.page.url).hostname or ""
        if host != urlparse(settings.yango_base_url).hostname:
            return False
        names = {c["name"] for c in await flow.context.cookies()}
        if any(n in names for n in self.cfg.get("session_cookies") or []):
            return True
        try:
            header = (await flow.page.inner_text("body"))[:400]
        except Exception:
            return False
        return not any(re.search(rf"\b{re.escape(t)}\b", header) for t in self.cfg.get("logged_out_texts") or [])

    async def _snapshot(self, flow: LoginFlow) -> Dict[str, Any]:
        if flow.state == "esperando" and await self._logged_in(flow):
            sessions.import_session(flow.account, await flow.context.storage_state())
            sessions.set_status(flow.account, "ok", "sesión iniciada con teléfono")
            db.event("login_ok", {"telefono": mask_phone(flow.phone)}, account=flow.account)
            flow.state, flow.detail = "listo", "Sesión iniciada y guardada"
        shot = ""
        try:
            raw = await flow.page.screenshot(type="jpeg", quality=70)
            shot = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
        except Exception:
            pass
        out = {"account": flow.account, "phone": mask_phone(flow.phone), "state": flow.state, "detail": flow.detail,
               "url": (urlparse(flow.page.url).hostname or ""), "screenshot": shot, "viewport": VIEWPORT}
        if flow.state in ("listo", "error"):
            await self.cancel(flow.account)
        return out
