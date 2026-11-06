"""Menú en PDF para el local (mesa, mostrador, volantes), con el menú sincronizado de Yango y un estilo editable.

El HTML se arma en el servidor con los precios del menú sincronizado; el PDF lo imprime Chromium (Playwright,
que ya viene en la imagen). Si Chromium no está disponible, la vista HTML se imprime desde el navegador.
"""
import hashlib
import logging
from typing import Any, Dict, List

import segno

from app import themes
from app.config import settings

log = logging.getLogger("pdfmenu")
PAGE_SIZES = {"A4": "A4", "Letter": "Letter", "A5": "A5"}
SHEET_MM = {"A4": (210, 297), "Letter": (216, 279), "A5": (148, 210)}  # ancho, alto en vertical


def category_key(name: str) -> str:
    return "c" + hashlib.sha1((name or "").strip().lower().encode()).hexdigest()[:10]


def categories(menu: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Categorías con su clave estable (para ocultarlas en el PDF) y cuántos productos tienen."""
    return [{"key": category_key(c["name"]), "name": c["name"], "count": len(c["items"])}
            for c in menu.get("categories") or [] if c.get("items")]


def build_sections(menu: Dict[str, Any], style: Dict[str, Any]) -> List[Dict[str, Any]]:
    hidden = set(style.get("hidden_categories") or [])
    sections = []
    for c in menu.get("categories") or []:
        if category_key(c["name"]) in hidden:
            continue
        items = []
        for iid in c.get("items") or []:
            it = (menu.get("items") or {}).get(iid)
            if not it or not it.get("available", True):
                continue
            promo = it.get("promo_price")
            items.append({
                "name": it["name"],
                "description": it.get("description") or "",
                "price": promo if promo is not None and promo < it["price"] else it["price"],
                "old_price": it["price"] if promo is not None and promo < it["price"] else None,
                "image": it.get("image") if (it.get("image") or "").startswith("https://") else None,
            })
        if items:
            sections.append({"name": c["name"], "items": items})
    return sections


def qr_svg(url: str, dark: str, light: str) -> str:
    return segno.make(url, error="m").svg_inline(scale=4, dark=dark, light=light, border=1)


def context(partner: Dict[str, Any], menu: Dict[str, Any], style: Dict[str, Any]) -> Dict[str, Any]:
    s = themes.merge_pdf(style)
    theme = themes.merge(partner.get("theme") or {})
    url = f"{settings.public_base_url}/{partner['slug']}"
    return {
        "p": partner, "s": s, "css_vars": themes.pdf_css_vars(s), "fonts_href": themes.pdf_fonts_href(s),
        "sections": build_sections(menu, s), "currency": settings.currency,
        "logo_url": theme.get("logo_url") if s["show_logo"] else "",
        "store_url": url, "store_url_short": url.split("://", 1)[-1],
        "qr": qr_svg(url, s["text"], s["background"]) if s["show_qr"] else "",
        "profile": partner.get("profile") or {},
        "page": f"{PAGE_SIZES[s['page_size']]} {s['orientation']}",
        "sheet_w": SHEET_MM[s["page_size"]][1 if s["orientation"] == "landscape" else 0],
    }


async def render_pdf(html: str, style: Dict[str, Any]) -> bytes:
    """HTML → PDF con Chromium. Las imágenes de /uploads se resuelven contra PUBLIC_BASE_URL."""
    from playwright.async_api import async_playwright

    s = themes.merge_pdf(style)
    async with async_playwright() as pw:
        kw: Dict[str, Any] = {"headless": True, "args": ["--no-sandbox"]}
        if settings.chromium_executable:
            kw["executable_path"] = settings.chromium_executable
        browser = await pw.chromium.launch(**kw)
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle", timeout=30000)
            await page.evaluate("document.fonts.ready")
            return await page.pdf(format=PAGE_SIZES[s["page_size"]], landscape=s["orientation"] == "landscape",
                                  print_background=True, prefer_css_page_size=True)
        finally:
            await browser.close()
