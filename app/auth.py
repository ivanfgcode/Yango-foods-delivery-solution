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
CHAIN_COOKIE = "cadena"
OPERATOR_COOKIE = "operador"
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


def chain_token(chain_id: int) -> str:
    return sign({"chain": int(chain_id), "exp": int(time.time()) + TTL})


def operator_token(login: str) -> str:
    return sign({"op": login, "exp": int(time.time()) + 12 * 3600})  # un turno


def current_chain(request: Request) -> Optional[int]:
    data = verify(request.cookies.get(CHAIN_COOKIE))
    return int(data["chain"]) if data and data.get("chain") else None


def require_chain(request: Request) -> int:
    cid = current_chain(request)
    if not cid:
        raise HTTPException(401, "Inicia sesión con el usuario de tu cadena")
    return cid


def current_operator(request: Request) -> Optional[str]:
    """Login del operador con sesión (o «admin» si entra el administrador). Revisa que siga activo."""
    if is_admin(request):
        return "admin"
    data = verify(request.cookies.get(OPERATOR_COOKIE))
    if not data or not data.get("op"):
        return None
    from app import operators  # evita import circular
    op = operators.get(data["op"])
    return op["login"] if op and op["enabled"] else None


def require_operator(request: Request) -> str:
    op = current_operator(request)
    if not op:
        raise HTTPException(401, "Inicia sesión como operador")
    return op


def is_admin(request: Request) -> bool:
    key = request.headers.get("x-admin-key")
    if key and hmac.compare_digest(key, settings.admin_key):
        return True
    data = verify(request.cookies.get(ADMIN_COOKIE))
    return bool(data and data.get("admin"))


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Requiere acceso de administrador")


def can_portal(request: Request, slug: str) -> bool:
    """Admin, sesión del portal de ese local o sesión de la cadena dueña del local."""
    if is_admin(request):
        return True
    data = verify(request.cookies.get(PORTAL_COOKIE))
    if data and data.get("slug") == slug:
        return True
    cid = current_chain(request)
    if cid:
        from app import partners  # evita import circular
        p = partners.get(slug)
        return bool(p and p.get("chain_id") == cid)
    return False


def require_portal(request: Request, slug: str) -> None:
    if not can_portal(request, slug):
        raise HTTPException(401, "Inicia sesión en el portal")
