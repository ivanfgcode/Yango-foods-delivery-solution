"""Cookies firmadas para el portal de cada partner y el panel de admin."""
import base64
import hashlib
import hmac
import json
import time
from typing import Optional

from fastapi import HTTPException, Request

from app.config import settings

PORTAL_COOKIE = "portal"
ADMIN_COOKIE = "admin"
TTL = 14 * 24 * 3600


def sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    mac = hmac.new(settings.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{mac}"


def verify(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    raw, mac = token.rsplit(".", 1)
    good = hmac.new(settings.secret_key.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, good):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data


def portal_token(slug: str) -> str:
    return sign({"slug": slug, "exp": int(time.time()) + TTL})


def admin_token() -> str:
    return sign({"admin": True, "exp": int(time.time()) + TTL})


def is_admin(request: Request) -> bool:
    key = request.headers.get("x-admin-key")
    if key and any(hmac.compare_digest(key, k) for k in [settings.admin_key, *settings.admin_keys_extra]):
        return True
    data = verify(request.cookies.get(ADMIN_COOKIE))
    return bool(data and data.get("admin"))


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Requiere acceso de administrador")


def require_portal(request: Request, slug: str) -> None:
    if is_admin(request):
        return
    data = verify(request.cookies.get(PORTAL_COOKIE))
    if not data or data.get("slug") != slug:
        raise HTTPException(401, "Inicia sesión en el portal")
