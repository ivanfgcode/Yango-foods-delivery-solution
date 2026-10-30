"""Partners (restaurantes ocultos) y su web de pedidos en dominio.com/<slug>."""
import hashlib
import hmac
import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app import db, themes

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$")
RESERVED = {"admin", "api", "static", "uploads", "health", "docs", "openapi.json", "portal", "favicon.ico"}
JSON_FIELDS = ("theme", "profile")


def parse_yango_url(url: str) -> Dict[str, Optional[str]]:
    """https://food.yango.com/bo/santa-cruz-de-la-sierra/r/wrap_roll_-_san_martin → brand_slug, city."""
    path = [p for p in urlparse(url or "").path.split("/") if p]
    if "r" in path and path.index("r") + 1 < len(path):
        i = path.index("r")
        return {"brand_slug": path[i + 1], "city": path[i - 1] if i >= 1 else None}
    return {"brand_slug": None, "city": None}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: Optional[str]) -> bool:
    try:
        _, salt, digest = (stored or "").split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return hmac.compare_digest(dk.hex(), digest)


def get(slug: str) -> Optional[Dict[str, Any]]:
    return db.row_dict(db.one("SELECT * FROM partners WHERE slug=?", (slug,)), JSON_FIELDS)


def get_by_id(pid: int) -> Optional[Dict[str, Any]]:
    return db.row_dict(db.one("SELECT * FROM partners WHERE id=?", (pid,)), JSON_FIELDS)


def all_partners() -> List[Dict[str, Any]]:
    return [db.row_dict(r, JSON_FIELDS) for r in db.query("SELECT * FROM partners ORDER BY name")]


def create(slug: str, name: str, account: str, yango_url: str = "", place_slug: str = "",
           password: Optional[str] = None, theme: Optional[Dict[str, Any]] = None,
           profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug) or slug in RESERVED:
        raise ValueError("Slug inválido: minúsculas, números y guiones")
    if not re.match(r"^[A-Za-z0-9_-]{1,40}$", account or ""):
        raise ValueError("Nombre de cuenta inválido")
    if get(slug):
        raise ValueError("Ya existe un partner con ese slug")
    parsed = parse_yango_url(yango_url)
    now = db.now_iso()
    db.execute(
        """INSERT INTO partners(slug, name, yango_url, brand_slug, place_slug, account, theme, profile,
           portal_password_hash, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (slug, name.strip(), yango_url.strip(), parsed["brand_slug"], place_slug.strip() or None, account,
         json.dumps(themes.validate(theme or {})), json.dumps(profile or {}, ensure_ascii=False),
         hash_password(password) if password else None, now, now),
    )
    db.execute("INSERT OR IGNORE INTO accounts(name) VALUES (?)", (account,))
    return get(slug)


def update(slug: str, **fields: Any) -> Dict[str, Any]:
    allowed = {"name", "yango_url", "place_slug", "place_id", "place_lat", "place_lon", "place_address", "place_name",
               "account", "enabled", "accepting_orders", "min_order", "brand_slug"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "yango_url":
            sets.append("brand_slug=?")
            vals.append(parse_yango_url(v)["brand_slug"])
        sets.append(f"{k}=?")
        vals.append(v)
    if sets:
        sets.append("updated_at=?")
        vals.append(db.now_iso())
        db.execute(f"UPDATE partners SET {', '.join(sets)} WHERE slug=?", (*vals, slug))
        if "account" in fields:
            db.execute("INSERT OR IGNORE INTO accounts(name) VALUES (?)", (fields["account"],))
    return get(slug)


def set_theme(slug: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    p = get(slug)
    theme = dict(p["theme"] or {})
    theme.update(themes.validate(patch))
    db.execute("UPDATE partners SET theme=?, updated_at=? WHERE slug=?", (json.dumps(theme), db.now_iso(), slug))
    return themes.merge(theme)


PROFILE_KEYS = {"tagline": 120, "about": 600, "whatsapp": 20, "instagram": 60, "facebook": 120,
                "hours_text": 120, "announcement": 160}


def set_profile(slug: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    p = get(slug)
    profile = dict(p["profile"] or {})
    for k, limit in PROFILE_KEYS.items():
        if k in patch:
            profile[k] = re.sub(r"[<>]", "", str(patch[k] or ""))[:limit].strip()
    db.execute("UPDATE partners SET profile=?, updated_at=? WHERE slug=?",
               (json.dumps(profile, ensure_ascii=False), db.now_iso(), slug))
    return profile


def set_tiktok(slug: str, videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Videos de TikTok de la tienda: [{id, url, title, author, poster, item_ids}] (máx. 12)."""
    p = get(slug)
    profile = dict(p["profile"] or {})
    clean = []
    for v in videos[:12]:
        if not re.fullmatch(r"\d{8,25}", str(v.get("id") or "")):
            continue
        clean.append({"id": str(v["id"]), "url": str(v.get("url") or "")[:300],
                      "title": re.sub(r"[<>]", "", str(v.get("title") or ""))[:150],
                      "author": re.sub(r"[<>]", "", str(v.get("author") or ""))[:60],
                      "poster": v.get("poster") if re.fullmatch(r"/uploads/[\w.-]+", str(v.get("poster") or "")) else None,
                      "item_ids": _ids(v.get("item_ids") or ([v["item_id"]] if v.get("item_id") else []), 6)})
    profile["tiktok"] = clean
    db.execute("UPDATE partners SET profile=?, updated_at=? WHERE slug=?",
               (json.dumps(profile, ensure_ascii=False), db.now_iso(), slug))
    return clean


def _ids(values: Any, limit: int) -> List[str]:
    out: List[str] = []
    for v in values if isinstance(values, list) else []:
        s = str(v)[:40]
        if re.fullmatch(r"[\w-]+", s) and s not in out:
            out.append(s)
    return out[:limit]


def set_bestsellers(slug: str, mode: str, items: List[Any]) -> Dict[str, Any]:
    """«Más vendidos» de la tienda: automático (estadísticas de pedidos) o elegido a mano (máx. 12)."""
    p = get(slug)
    profile = dict(p["profile"] or {})
    profile["bestsellers"] = {"mode": "manual" if mode == "manual" else "auto", "items": _ids(items, 12)}
    db.execute("UPDATE partners SET profile=?, updated_at=? WHERE slug=?",
               (json.dumps(profile, ensure_ascii=False), db.now_iso(), slug))
    return profile["bestsellers"]


def set_password(slug: str, password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    db.execute("UPDATE partners SET portal_password_hash=?, updated_at=? WHERE slug=?",
               (hash_password(password), db.now_iso(), slug))
