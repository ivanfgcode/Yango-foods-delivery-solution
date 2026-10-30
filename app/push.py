"""Notificaciones Web Push al cliente (VAPID). Las claves salen de VAPID_PRIVATE_KEY/VAPID_PUBLIC_KEY o se generan una
vez en data/vapid.json. Funciona en Android/Chrome y en iPhone con la web instalada en la pantalla de inicio (iOS 16.4+)."""
import asyncio
import base64
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app import db
from app.config import settings

log = logging.getLogger("push")
_keys: Optional[Dict[str, str]] = None


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def keys() -> Dict[str, str]:
    """{'private_pem', 'public'}: public en base64url (punto sin comprimir), el formato que pide pushManager.subscribe."""
    global _keys
    if _keys:
        return _keys
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01
    if settings.vapid_private_key:
        pem = settings.vapid_private_key.replace("\\n", "\n")
    else:
        path = settings.data_dir / "vapid.json"
        if path.exists():
            pem = json.loads(path.read_text())["private_pem"]
        else:
            v = Vapid01()
            v.generate_keys()
            pem = v.private_pem().decode()
            settings.ensure_dirs()
            path.write_text(json.dumps({"private_pem": pem}))
    v = Vapid01.from_pem(pem.encode())
    public = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _keys = {"private_pem": pem, "public": _b64(public)}
    return _keys


def subscribe(order_token: str, sub: Dict[str, Any]) -> None:
    endpoint = str(sub.get("endpoint") or "")
    k = sub.get("keys") or {}
    if urlparse(endpoint).scheme != "https" or len(endpoint) > 1000 or not k.get("p256dh") or not k.get("auth"):
        raise ValueError("Suscripción inválida")
    db.execute(
        """INSERT INTO push_subs(endpoint, order_token, p256dh, auth, created_at) VALUES (?,?,?,?,?)
           ON CONFLICT(endpoint) DO UPDATE SET order_token=excluded.order_token, p256dh=excluded.p256dh,
           auth=excluded.auth""",
        (endpoint, order_token, str(k["p256dh"])[:200], str(k["auth"])[:100], db.now_iso()))


def count(order_token: str) -> int:
    return db.one("SELECT count(*) n FROM push_subs WHERE order_token=?", (order_token,))["n"]


# Para pruebas: si se define, recibe (suscripción, payload) en lugar de enviar.
sender = None


def _send_one(sub: Dict[str, Any], payload: str) -> Optional[int]:
    if sender:
        sender(sub, payload)
        return 201
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush
    host = urlparse(sub["endpoint"])
    try:
        webpush({"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}, payload,
                vapid_private_key=Vapid01.from_pem(keys()["private_pem"].encode()),
                vapid_claims={"sub": settings.vapid_subject, "aud": f"{host.scheme}://{host.netloc}"}, ttl=3600)
        return 201
    except WebPushException as e:
        return getattr(e.response, "status_code", None)


async def send(order_token: str, title: str, body: str, url: str, icon: Optional[str] = None) -> int:
    """Envía a todos los teléfonos suscritos al pedido. Borra las suscripciones que ya no existen (404/410)."""
    subs = [dict(r) for r in db.query("SELECT * FROM push_subs WHERE order_token=?", (order_token,))]
    payload = json.dumps({"title": title, "body": body, "url": url, "icon": icon, "tag": order_token},
                         ensure_ascii=False)
    sent = 0
    for s in subs:
        try:
            code = await asyncio.to_thread(_send_one, s, payload)
        except Exception as e:  # pragma: no cover - red
            log.warning("push %s: %s", s["endpoint"][:60], e)
            continue
        if code in (404, 410):
            db.execute("DELETE FROM push_subs WHERE endpoint=?", (s["endpoint"],))
        elif code and code < 300:
            sent += 1
    return sent
