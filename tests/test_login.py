"""Login asistido con teléfono contra un Yango ID simulado (páginas locales con Playwright, sin red)."""
import asyncio
import json
from urllib.parse import urlparse

import pytest

from app.config import settings
from bot import login, sessions

PHONE_PAGE = """<html><body><input data-testid="text-field-input" type="tel" value="+1 ">
<button data-testid="phone-next" onclick="location.href='/code?p='+encodeURIComponent(document.querySelector('input').value)">Continue</button>
<button>Aceptar</button></body></html>"""
CODE_PAGE = """<html><body><p id="p"></p><input inputmode="numeric" autocomplete="one-time-code">
<script>document.getElementById('p').textContent = new URLSearchParams(location.search).get('p');
document.querySelector('input').addEventListener('input', (e) => {
  if (e.target.value === '123456') location.href = 'https://food.yango.test/es-bo/?auth=1';
});</script></body></html>"""
FOOD_PAGE = """<html><body><script>document.cookie = 'Session_id=abc; path=/';</script>Hola</body></html>"""

CFG = {"url": "https://passport.yango.test/auth?retpath={retpath}", "phone_input": "[data-testid=text-field-input]",
       "phone_next": "[data-testid=phone-next]", "code_input": "input[inputmode=numeric]",
       "cookie_accept": ["Aceptar"], "session_cookies": ["Session_id"], "logged_out_texts": ["Log in"]}


def test_normalize_phone():
    assert login.normalize_phone("7 123-4567") == "+59171234567"
    assert login.normalize_phone("+591 61234567") == "+59161234567"
    for bad in ("", "1234", "51234567", "+591 7123456"):
        with pytest.raises(login.LoginError):
            login.normalize_phone(bad)
    assert login.mask_phone("+59171234567") == "+5917123****"


def test_login_flow_saves_session(fresh_db, monkeypatch):
    pw_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(settings, "yango_base_url", "https://food.yango.test")

    async def run():
        async with pw_api.async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(executable_path=settings.chromium_executable or None)
            except Exception as e:  # pragma: no cover
                pytest.skip(f"sin Chromium: {e}")
            # Las páginas «de Yango» se sirven localmente en cada contexto que abre el login.
            async def route(r):
                url = urlparse(r.request.url)
                body = FOOD_PAGE if url.hostname == "food.yango.test" else CODE_PAGE if url.path == "/code" else PHONE_PAGE
                await r.fulfill(status=200, content_type="text/html", body=body)
            real_new_context = browser.new_context

            async def new_context(**kw):
                ctx = await real_new_context(**kw)
                await ctx.route("**/*", route)
                return ctx
            monkeypatch.setattr(browser, "new_context", new_context)
            mgr = login.LoginManager(CFG, browser=browser)

            s = await mgr.start("wrap_roll", "71234567")
            assert s["state"] == "esperando", s
            assert s["screenshot"].startswith("data:image/jpeg;base64,")
            assert s["phone"] == "+5917123****"
            flow = mgr.flows["wrap_roll"]
            assert "+59171234567" in (await flow.page.inner_text("#p")).replace(" ", "")

            with pytest.raises(login.LoginError):
                await mgr.act("wrap_roll", {"type": "key", "key": "F12"})
            s = await mgr.act("wrap_roll", {"type": "code", "code": "123 456"})
            assert s["state"] == "listo", s
            assert "wrap_roll" not in mgr.flows
            await browser.close()

    asyncio.run(run())
    state = json.loads(sessions.session_path("wrap_roll").read_text())
    assert any(c["name"] == "Session_id" for c in state["cookies"])


def test_login_endpoints_need_admin(client_no_sync):
    c = client_no_sync
    assert c.post("/api/admin/accounts/x/login", json={"phone": "71234567"}).status_code == 401
    assert c.get("/api/admin/accounts/x/login").status_code == 401
    r = c.get("/api/admin/accounts/x/login", headers={"x-admin-key": settings.admin_key})
    assert r.status_code == 422 and "No hay" in r.json()["detail"]
    r = c.post("/api/admin/accounts/x/login", json={"phone": "123"}, headers={"x-admin-key": settings.admin_key})
    assert r.status_code == 422


@pytest.fixture()
def client_no_sync(fresh_db):
    from fastapi.testclient import TestClient
    from app import api
    api.app.state.background = False
    with TestClient(api.app) as c:
        yield c
