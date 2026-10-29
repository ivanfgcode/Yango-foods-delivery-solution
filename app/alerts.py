"""Avisos al equipo por Telegram (si está configurado). Nunca rompen el flujo."""
import logging

import httpx

from app import db
from app.config import settings

log = logging.getLogger("alerts")


async def alert(kind: str, text: str) -> None:
    db.event(f"alerta_{kind}", {"text": text})
    log.warning("[%s] %s", kind, text)
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                         json={"chat_id": settings.telegram_chat_id, "text": text[:3900],
                               "disable_web_page_preview": True})
    except Exception as e:  # pragma: no cover - red
        log.error("telegram: %s", e)
