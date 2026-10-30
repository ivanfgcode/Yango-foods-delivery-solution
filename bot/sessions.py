"""Sesiones de las cuentas cliente de Yango.

El login de food.yango.com es por teléfono + SMS y suele pedir captcha: el bot no lo hace solo. Una persona entra
en su navegador con la cuenta y exporta las cookies en JSON (extensión «Cookie-Editor» → Export → JSON, o el
`storage_state` de Playwright). Se cargan en el panel (Admin → Cuentas) y quedan en `data/sessions/<cuenta>.json`.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from app import db
from app.config import settings

_SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict", "unspecified": "Lax"}
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")


class SessionImportError(ValueError):
    pass


def session_path(account: str) -> Path:
    if not NAME_RE.match(account):
        raise SessionImportError("Nombre de cuenta inválido")
    return settings.sessions_dir / f"{account}.json"


def _convert_cookie(c: Dict[str, Any]) -> Dict[str, Any]:
    if not c.get("name") or "value" not in c or not c.get("domain"):
        raise SessionImportError("Cookie sin name/value/domain")
    out = {
        "name": str(c["name"]), "value": str(c["value"]), "domain": str(c["domain"]),
        "path": str(c.get("path") or "/"),
        "httpOnly": bool(c.get("httpOnly", False)), "secure": bool(c.get("secure", False)),
        "sameSite": _SAMESITE.get(str(c.get("sameSite") or "lax").lower(), c.get("sameSite") or "Lax"),
    }
    exp = c.get("expires", c.get("expirationDate"))
    out["expires"] = float(exp) if exp not in (None, "", -1) and not c.get("session") else -1
    if out["sameSite"] not in ("None", "Lax", "Strict"):
        out["sameSite"] = "Lax"
    return out


def from_cookie_header(text: str) -> List[Dict[str, Any]]:
    """Línea «Cookie: a=1; b=2» copiada de las herramientas del navegador (pestaña Red) → cookies de .yango.com."""
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:]
    cookies = []
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not COOKIE_NAME_RE.match(name):
            raise SessionImportError("No es JSON ni una línea «Cookie: nombre=valor; ...»")
        cookies.append({"name": name, "value": value, "domain": ".yango.com", "path": "/", "secure": True})
    if not cookies:
        raise SessionImportError("No hay cookies")
    return cookies


def to_storage_state(raw: Any) -> Dict[str, Any]:
    """Acepta: lista de cookies (Cookie-Editor / EditThisCookie), storage_state de Playwright o línea «Cookie:»."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = from_cookie_header(raw)
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        cookies: List[Dict[str, Any]] = raw["cookies"]
        origins = raw.get("origins") or []
    elif isinstance(raw, list):
        cookies, origins = raw, []
    else:
        raise SessionImportError("Formato no reconocido: se espera una lista de cookies o un storage_state")
    conv = [_convert_cookie(c) for c in cookies]
    yango = [c for c in conv if "yango" in c["domain"] or "yandex" in c["domain"]]
    if not yango:
        raise SessionImportError("No hay cookies de yango.com en el archivo")
    return {"cookies": conv, "origins": origins}


def import_session(account: str, raw: Any) -> Dict[str, Any]:
    state = to_storage_state(raw)
    path = session_path(account)
    settings.ensure_dirs()
    path.write_text(json.dumps(state))
    now = db.now_iso()
    db.execute(
        """INSERT INTO accounts(name, status, detail, session_imported_at) VALUES (?, 'importada', NULL, ?)
           ON CONFLICT(name) DO UPDATE SET status='importada', detail=NULL, session_imported_at=excluded.session_imported_at""",
        (account, now),
    )
    db.event("sesion_importada", {"cookies": len(state["cookies"])}, account=account)
    return {"account": account, "cookies": len(state["cookies"])}


def has_session(account: str) -> bool:
    try:
        return session_path(account).exists()
    except SessionImportError:
        return False


def set_status(account: str, status: str, detail: str = "") -> None:
    db.execute(
        """INSERT INTO accounts(name, status, detail, checked_at) VALUES (?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET status=excluded.status, detail=excluded.detail, checked_at=excluded.checked_at""",
        (account, status, detail[:300], db.now_iso()),
    )
