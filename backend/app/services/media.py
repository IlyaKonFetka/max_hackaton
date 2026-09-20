"""Фото: сохранение из мини-приложения и скачивание из сообщений MAX. В БД — только путь.

При сохранении снимок нормализуется: применяется EXIF-ориентация (иначе в PDF фото с телефона
лежит на боку — reportlab EXIF не читает), длинная сторона ужимается до 1600 px, EXIF отбрасывается.
"""

from __future__ import annotations

import io
import secrets
from pathlib import Path

import httpx

from ..config import settings

ALLOWED = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic"}
MAX_BYTES = 15 * 1024 * 1024
MAX_SIDE = 1600
JPEG_QUALITY = 85


def _new_name(ext: str) -> Path:
    return settings.photos_dir / f"{secrets.token_hex(8)}{ext}"


def _normalize(content: bytes) -> tuple[bytes, str] | None:
    """Поворот по EXIF + уменьшение + перекодирование в JPEG. None — если Pillow не смог открыть (например, HEIC)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover
        return None
    try:
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img) or img
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return out.getvalue(), ".jpg"
    except Exception:  # noqa: BLE001
        return None


def save_upload(content: bytes, content_type: str | None, filename: str | None = None) -> str:
    if len(content) > MAX_BYTES:
        raise ValueError("файл больше 15 МБ")
    ext = ALLOWED.get((content_type or "").split(";")[0].strip().lower())
    if ext is None:
        suffix = Path(filename or "").suffix.lower()
        if suffix in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
            ext = ".jpg" if suffix == ".jpeg" else suffix
        else:
            raise ValueError("допустимы только изображения (jpeg, png, webp, heic)")
    normalized = _normalize(content)
    if normalized is not None:
        content, ext = normalized
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
