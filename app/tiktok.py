"""Videos de TikTok en la tienda: el partner pega el enlace y elige el producto que aparece.

No hace falta app de desarrollador: el enlace se resuelve con el oEmbed público de TikTok
(https://www.tiktok.com/oembed) y en la tienda se usa su reproductor (https://www.tiktok.com/player/v1/<id>).
La portada que da oEmbed caduca, así que se guarda una copia en data/uploads. Listar los videos de una cuenta
de forma automática sí necesitaría la Display API de TikTok (app + login del dueño): aquí se eligen a mano.
"""
import re
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from app.config import settings

VIDEO_RE = re.compile(r"/(?:video|photo)/(\d{8,25})")
HOSTS = ("tiktok.com",)
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
UA = "Mozilla/5.0 (pedidos-propios)"
MAX_VIDEOS = 12


class TikTokError(ValueError):
    pass


def _tiktok_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in HOSTS)


async def resolve(url: str, slug: str) -> Dict[str, Any]:
    """Enlace (completo o corto vm./vt.tiktok.com) → {id, url, title, author, poster}."""
    url = (url or "").strip()
    if not url.startswith("https://") or not _tiktok_host(url):
        raise TikTokError("Pega un enlace de un video de tiktok.com")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"user-agent": UA}) as c:
        m = VIDEO_RE.search(urlparse(url).path)
        if not m:  # enlace corto: seguir la redirección hasta el video
            try:
                r = await c.get(url)
            except httpx.HTTPError:
                raise TikTokError("No se pudo abrir el enlace de TikTok")
            final = str(r.url)
            m = VIDEO_RE.search(urlparse(final).path) if _tiktok_host(final) else None
            if not m:
                raise TikTokError("El enlace no lleva a un video de TikTok")
            url = final.split("?")[0]
        vid = m.group(1)
        meta: Dict[str, Any] = {}
        try:
            r = await c.get("https://www.tiktok.com/oembed", params={"url": url})
        except httpx.HTTPError:
            r = None  # sin oEmbed igual se puede reproducir con el id; solo falta la portada
        if r is not None and r.status_code in (400, 404):
            raise TikTokError("TikTok no encuentra ese video (¿es privado o se borró?)")
        if r is not None and r.status_code == 200:
            try:
                meta = r.json()
            except ValueError:
                meta = {}
        poster = await _save_poster(c, meta.get("thumbnail_url"), slug)
    author = meta.get("author_unique_id") or meta.get("author_name") or ""
    if not author:
        am = re.search(r"/@([\w.-]{2,40})/", urlparse(url).path)
        author = am.group(1) if am else ""
    return {"id": vid, "url": f"https://www.tiktok.com/video/{vid}" if "/video/" not in url else url.split("?")[0],
            "title": str(meta.get("title") or "")[:150], "author": str(author)[:60],
            "poster": poster}


async def _save_poster(c: httpx.AsyncClient, thumb: Optional[str], slug: str) -> Optional[str]:
    if not thumb or not thumb.startswith("https://"):
        return None
    try:
        r = await c.get(thumb)
    except httpx.HTTPError:
        return None
    ext = IMAGE_TYPES.get((r.headers.get("content-type") or "").split(";")[0])
    if r.status_code != 200 or not ext or len(r.content) > 5 * 1024 * 1024:
        return None
    name = f"{slug}-tt-{secrets.token_hex(6)}.{ext}"
    settings.ensure_dirs()
    (settings.uploads_dir / name).write_bytes(r.content)
    return f"/uploads/{name}"
