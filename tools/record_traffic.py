"""Graba lo que hace food.yango.com mientras una persona coloca un pedido de prueba a mano con una cuenta.

Sirve para completar `order` en config/yango_web.yaml (carrito, checkout, número de pedido).

    python -m tools.record_traffic --account wrap_roll --url https://food.yango.com/bo/.../r/<marca>

Abre un navegador visible con la sesión de la cuenta (data/sessions/<cuenta>.json). Se hace el pedido a mano
(efectivo, comentario de prueba) y se cierra la ventana. Queda data/recordings/<cuenta>-<hora>.jsonl con cada
petición a /api/ y /eats/ (método, URL, cuerpo enviado y respuesta).
"""
import argparse
import asyncio
import json
import time

from app.config import settings
from bot import sessions


async def main(account: str, url: str) -> None:
    from playwright.async_api import async_playwright
    out_dir = settings.data_dir / "recordings"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{account}-{int(time.time())}.jsonl"
    path = sessions.session_path(account)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, executable_path=settings.chromium_executable or None)
        ctx = await browser.new_context(storage_state=str(path) if path.exists() else None, locale="es-BO")
        page = await ctx.new_page()
        f = out.open("w")

        async def on_response(resp):
            u = resp.url
            if "/api/" not in u and "/eats/" not in u:
                return
            try:
                body = await resp.text()
            except Exception:
                body = ""
            f.write(json.dumps({"t": time.time(), "method": resp.request.method, "url": u, "status": resp.status,
                                "request": resp.request.post_data, "response": body[:20000]},
                               ensure_ascii=False) + "\n")
            f.flush()

        page.on("response", on_response)
        await page.goto(url)
        print(f"Grabando en {out}. Haz el pedido de prueba y cierra la ventana.")
        await page.wait_for_event("close", timeout=0)
        await ctx.storage_state(path=str(path))  # guarda la sesión renovada
        f.close()
        await browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--url", required=True)
    a = ap.parse_args()
    asyncio.run(main(a.account, a.url))
