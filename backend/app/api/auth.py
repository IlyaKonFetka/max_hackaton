"""Проверка подлинности данных мини-приложения (initData из MAX Bridge).

Алгоритм по dev.max.ru/docs/webapps/validation:
  secret     = HMAC-SHA256(key="WebAppData", msg=BOT_TOKEN)
  check_str  = отсортированные по ключу пары "key=value" без hash, через "\n" (значения URL-декодированы)
  hash       = hex(HMAC-SHA256(key=secret, msg=check_str))
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException

from ..config import settings

MAX_AGE_SEC = 24 * 3600


@dataclass(frozen=True)
class MaxUser:
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str | None = None
    start_param: str = ""


def _data_check_string(pairs: list[tuple[str, str]]) -> str:
    items = sorted((k, v) for k, v in pairs if k != "hash")
    return "\n".join(f"{k}={v}" for k, v in items)


def verify_init_data(init_data: str, token: str) -> dict:
    pairs = parse_qsl(init_data, keep_blank_values=True)
    given = dict(pairs).get("hash")
    if not given:
        raise ValueError("нет hash")
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, _data_check_string(pairs).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, given):
        raise ValueError("подпись не совпадает")
    data = dict(pairs)
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if auth_date and time.time() - auth_date > MAX_AGE_SEC:
        raise ValueError("initData устарел")
    return data


def _user_from_data(data: dict) -> MaxUser:
    raw = data.get("user")
    if not raw:
        raise ValueError("нет user")
    u = json.loads(raw)
    return MaxUser(
        id=int(u["id"]),
        first_name=u.get("first_name") or "",
        last_name=u.get("last_name") or "",
        username=u.get("username"),
        start_param=data.get("start_param") or "",
    )


async def current_user(
    x_max_init_data: str | None = Header(default=None, alias="X-Max-Init-Data",
                                         description="initData мини-приложения MAX (подписан ботом) — основной способ"),
    x_api_key: str | None = Header(default=None, alias="X-Api-Key",
                                   description="Ключ тестового доступа для автоматизированной проверки"),
    authorization: str | None = Header(default=None, description="Альтернатива X-Api-Key: `Bearer <ключ>`"),
    x_debug_user: str | None = Header(default=None, alias="X-Debug-User", include_in_schema=False),
) -> MaxUser:
    key = x_api_key or (authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None)
    if key:
        uid = settings.api_test_keys.get(key)
        if uid is None:
            raise HTTPException(status_code=401, detail="неверный ключ доступа")
        return MaxUser(id=uid, first_name="Эксперт", last_name="(тестовый доступ)")
    if x_max_init_data:
        try:
            data = verify_init_data(x_max_init_data, settings.bot_token)
            return _user_from_data(data)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=f"initData: {e}") from e
    if settings.debug_auth and x_debug_user:
        # Только для локальной разработки: DEBUG_AUTH=1 и заголовок X-Debug-User: <user_id>[:start_param]
        uid, _, start = x_debug_user.partition(":")
        return MaxUser(id=int(uid), first_name="Debug", start_param=start)
    raise HTTPException(status_code=401, detail="нет данных авторизации MAX")
