"""Tema visual de cada web de pedidos. El partner lo edita en su portal; se publica como variables CSS."""
import re
from typing import Any, Dict

FONTS = {
    "Plus Jakarta Sans": "Plus+Jakarta+Sans:wght@400;500;600;700;800",
    "Figtree": "Figtree:wght@400;500;600;700;800",
    "Manrope": "Manrope:wght@400;500;600;700;800",
    "Inter": "Inter:wght@400;500;600;700;800",
    "Poppins": "Poppins:wght@400;500;600;700;800",
    "Montserrat": "Montserrat:wght@400;500;600;700;800",
    "Nunito": "Nunito:wght@400;600;700;800",
    "DM Sans": "DM+Sans:wght@400;500;700",
    "Outfit": "Outfit:wght@400;500;600;700;800",
    "Rubik": "Rubik:wght@400;500;600;700;800",
    "Playfair Display": "Playfair+Display:wght@500;700;800",
    "Lora": "Lora:wght@400;600;700",
    "Bebas Neue": "Bebas+Neue",
    "Fredoka": "Fredoka:wght@400;500;600;700",
}

DEFAULTS: Dict[str, Any] = {
    "primary": "#F25C54",        # botones, precios, acentos
    "on_primary": "#FFFFFF",     # texto sobre el color principal
    "background": "#FFFFFF",
    "surface": "#F4F4F5",        # tarjetas
    "text": "#141414",
    "muted": "#6B6B70",
    "heading_font": "Plus Jakarta Sans",  # geométrica, estilo apps de delivery (Uber Move no es libre)
    "body_font": "Plus Jakarta Sans",
    "radius": 18,                # px, esquinas de tarjetas y botones
    "card_layout": "list",       # list (filas, estilo apps de delivery) | grid
    "card_size": "m",            # s | m | l
    "image_ratio": "4/3",        # 1/1 | 4/3 | 16/9
    "hero_style": "cover",       # cover (foto grande) | compact | minimal
    "base_font_size": 16,        # px
    "dark_header": False,
    "logo_url": "",
    "cover_url": "",
}

CHOICES = {
    "card_layout": ("grid", "list"),
    "card_size": ("s", "m", "l"),
    "image_ratio": ("1/1", "4/3", "16/9"),
    "hero_style": ("cover", "compact", "minimal"),
}
THUMBS = {"s": "88px", "m": "118px", "l": "150px"}  # foto en el diseño de lista
COLOR_KEYS = ("primary", "on_primary", "background", "surface", "text", "muted")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_URL = re.compile(r"^(https://[^\s\"'<>()]{3,500}|/uploads/[A-Za-z0-9._-]{1,120})$")


def merge(theme: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (theme or {}).items() if k in DEFAULTS})
    return out


def validate(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Solo deja pasar valores seguros (se inyectan en CSS y en atributos)."""
    clean: Dict[str, Any] = {}
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k in COLOR_KEYS:
            if not isinstance(v, str) or not _HEX.match(v):
                raise ValueError(f"Color inválido en {k}")
            clean[k] = v.upper()
        elif k in ("heading_font", "body_font"):
            if v not in FONTS:
                raise ValueError(f"Tipografía no disponible: {v}")
            clean[k] = v
        elif k == "radius":
            clean[k] = max(0, min(32, int(v)))
        elif k == "base_font_size":
            clean[k] = max(14, min(19, int(v)))
        elif k in CHOICES:
            if v not in CHOICES[k]:
                raise ValueError(f"Valor inválido en {k}")
            clean[k] = v
        elif k == "dark_header":
            clean[k] = bool(v)
        elif k in ("logo_url", "cover_url"):
            v = (v or "").strip()
            if v and not _URL.match(v):
                raise ValueError(f"URL inválida en {k} (debe ser https)")
            clean[k] = v
    return clean


def css_vars(theme: Dict[str, Any]) -> str:
    t = merge(theme)
    size = {"s": "190px", "m": "240px", "l": "310px"}[t["card_size"]]
    thumb = THUMBS[t["card_size"]]
    return (
        f"--primary:{t['primary']};--on-primary:{t['on_primary']};--bg:{t['background']};"
        f"--surface:{t['surface']};--text:{t['text']};--muted:{t['muted']};"
        f"--radius:{t['radius']}px;--card-min:{size};--thumb:{thumb};--img-ratio:{t['image_ratio']};"
        f"--font-heading:'{t['heading_font']}',system-ui,sans-serif;"
        f"--font-body:'{t['body_font']}',system-ui,sans-serif;--base-size:{t['base_font_size']}px;"
    )


def fonts_href(theme: Dict[str, Any]) -> str:
    t = merge(theme)
    fams = {FONTS[t["heading_font"]], FONTS[t["body_font"]]}
    return "https://fonts.googleapis.com/css2?" + "&".join(f"family={f}" for f in sorted(fams)) + "&display=swap"


# ------------------------------------------------------------------------------------------- menú PDF
PDF_DEFAULTS: Dict[str, Any] = {
    "template": "clasico",       # clasico | moderno | minimal | pizarra
    "primary": "#F25C54",
    "background": "#FFFFFF",
    "text": "#1D1B19",
    "muted": "#6F6A64",
    "heading_font": "Playfair Display",
    "body_font": "Lora",
    "columns": 2,                # 1 | 2 | 3
    "page_size": "A4",           # A4 | Letter | A5
    "orientation": "portrait",   # portrait | landscape
    "font_size": 11,             # pt
    "leader": "dots",            # dots (puntos entre nombre y precio) | line | none
    "show_images": False,
    "show_descriptions": True,
    "show_logo": True,
    "show_qr": True,             # QR a la web de pedidos
    "show_promo": True,          # precio promo tachado
    "title": "",                 # vacío = nombre del restaurante
    "subtitle": "",
    "footer": "",
    "hidden_categories": [],     # ids de categorías que no van al PDF
}
PDF_CHOICES = {
    "template": ("clasico", "moderno", "minimal", "pizarra"),
    "page_size": ("A4", "Letter", "A5"),
    "orientation": ("portrait", "landscape"),
    "leader": ("dots", "line", "none"),
}
PDF_COLORS = ("primary", "background", "text", "muted")
PDF_TEXTS = {"title": 60, "subtitle": 120, "footer": 240}
PDF_BOOLS = ("show_images", "show_descriptions", "show_logo", "show_qr", "show_promo")
# Plantillas = punto de partida (colores y letras). El editor permite cambiarlas después.
PDF_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "clasico": {"background": "#FFFDF8", "text": "#1D1B19", "muted": "#6F6A64",
                "heading_font": "Playfair Display", "body_font": "Lora", "leader": "dots"},
    "moderno": {"background": "#FFFFFF", "text": "#141414", "muted": "#6B6B70",
                "heading_font": "Outfit", "body_font": "Inter", "leader": "none"},
    "minimal": {"background": "#FFFFFF", "text": "#222222", "muted": "#8A8A8A",
                "heading_font": "DM Sans", "body_font": "DM Sans", "leader": "line"},
    "pizarra": {"background": "#1F2421", "text": "#F3F1EA", "muted": "#B9B6AC",
                "heading_font": "Bebas Neue", "body_font": "Nunito", "leader": "dots"},
}
_CAT_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def merge_pdf(style: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(PDF_DEFAULTS)
    out.update({k: v for k, v in (style or {}).items() if k in PDF_DEFAULTS})
    return out


def validate_pdf(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Igual que validate(): solo valores seguros, porque van a CSS del PDF."""
    clean: Dict[str, Any] = {}
    for k, v in (patch or {}).items():
        if k not in PDF_DEFAULTS:
            continue
        if k in PDF_COLORS:
            if not isinstance(v, str) or not _HEX.match(v):
                raise ValueError(f"Color inválido en {k}")
            clean[k] = v.upper()
        elif k in ("heading_font", "body_font"):
            if v not in FONTS:
                raise ValueError(f"Tipografía no disponible: {v}")
            clean[k] = v
        elif k in PDF_CHOICES:
            if v not in PDF_CHOICES[k]:
                raise ValueError(f"Valor inválido en {k}")
            clean[k] = v
        elif k == "columns":
            clean[k] = max(1, min(3, int(v)))
        elif k == "font_size":
            clean[k] = max(8, min(16, int(v)))
        elif k in PDF_BOOLS:
            clean[k] = bool(v)
        elif k in PDF_TEXTS:
            clean[k] = re.sub(r"[<>]", "", str(v or "")).strip()[:PDF_TEXTS[k]]
        elif k == "hidden_categories":
            if not isinstance(v, list) or not all(isinstance(x, str) and _CAT_ID.match(x) for x in v):
                raise ValueError("Categorías ocultas inválidas")
            clean[k] = v[:200]
    return clean


def pdf_css_vars(style: Dict[str, Any]) -> str:
    s = merge_pdf(style)
    return (f"--primary:{s['primary']};--bg:{s['background']};--text:{s['text']};--muted:{s['muted']};"
            f"--font-heading:'{s['heading_font']}',Georgia,serif;--font-body:'{s['body_font']}',system-ui,sans-serif;"
            f"--cols:{s['columns']};--fs:{s['font_size']}pt;")


def pdf_fonts_href(style: Dict[str, Any]) -> str:
    s = merge_pdf(style)
    fams = {FONTS[s["heading_font"]], FONTS[s["body_font"]]}
    return "https://fonts.googleapis.com/css2?" + "&".join(f"family={f}" for f in sorted(fams)) + "&display=swap"
