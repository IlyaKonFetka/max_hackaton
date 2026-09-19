"""Экземпляр бота и диспетчера — общие для polling и для API (акт уходит в чат из API)."""

from __future__ import annotations

from maxapi import Bot, Dispatcher

from ..config import settings

bot = Bot(settings.bot_token or "no-token")
dp = Dispatcher()
