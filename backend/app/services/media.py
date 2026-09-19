"""Фото: сохранение из мини-приложения и скачивание из сообщений MAX. В БД — только путь."""

from __future__ import annotations

import secrets
from pathlib import Path

import httpx

from ..config import settings

ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}
MAX_BYTES = 10 * 1024 * 1024


def _new_name(ext: str) -> Path:
    return settings.photos_dir / f"{secrets.token_hex(8)}{ext}"


def save_upload(content: bytes, content_type: str | None, filename: str | None = None) -> str:
    if len(content) > MAX_BYTES:
        raise ValueError("файл больше 10 МБ")
    ext = ALLOWED.get((content_type or "").split(";")[0].strip().lower())
    if ext is None:
        # Пробуем по расширению имени файла.
        suffix = Path(filename or "").suffix.lower()
        if suffix in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            ext = ".jpg" if suffix == ".jpeg" else suffix
        else:
            raise ValueError("допустимы только изображения (jpeg, png, webp, heic)")
    p = _new_name(ext)
    p.write_bytes(content)
    return str(p.relative_to(settings.data_dir).as_posix())


async def download_from_max(url: str) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return save_upload(r.content, r.headers.get("content-type"), filename=url.split("?")[0])


def abs_path(rel: str) -> Path:
    return settings.data_dir / rel
