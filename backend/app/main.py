"""Точка входа: FastAPI (API мини-приложения + статика) и бот (long polling) в одном процессе."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .db import init_db
from .rulebook import get_rulebook

if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    book = get_rulebook()
    log.info("справочник: версия %s, требований %d, полей профиля %d", book.version, len(book.rules), len(book.fields))
    tasks: list[asyncio.Task] = []
    if settings.run_bot and settings.bot_token:
        from .bot import handlers  # noqa: F401  регистрирует обработчики
        from .bot.client import bot, dp
        from .jobs.reminders import reminder_loop

        me = await bot.get_me()
        log.info("бот: @%s (id=%s)", me.username, me.user_id)
        try:
            from maxapi.types import BotCommand

            await bot.set_commands(
                BotCommand(name="start", description="Начать или показать статус"),
                BotCommand(name="status", description="Статус заведения"),
                BotCommand(name="tasks", description="Открытые задачи"),
                BotCommand(name="shift", description="Чек-лист смены"),
                BotCommand(name="act", description="Последний акт"),
                BotCommand(name="profile", description="Заполнить профиль заново"),
                BotCommand(name="help", description="Справка"),
            )
        except Exception as e:  # noqa: BLE001
            log.warning("не удалось установить команды: %s", e)
        tasks.append(asyncio.create_task(dp.start_polling(bot), name="bot-polling"))
        tasks.append(asyncio.create_task(reminder_loop(), name="reminders"))
    elif not settings.bot_token:
        log.warning("MAX_BOT_TOKEN не задан — бот не запущен, работает только API")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


app = FastAPI(title="Движок применимости — API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"https://.*\.github\.io",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health():
    book = get_rulebook()
    return {"ok": True, "rules": len(book.rules), "rules_version": book.version}


# Фото — по случайным именам, только чтение.
app.mount("/media", StaticFiles(directory=str(settings.data_dir)), name="media")

# Собранное мини-приложение (если есть) — на /app, чтобы решение поднималось целиком из compose.
if settings.static_dir.exists():
    app.mount("/app", StaticFiles(directory=str(settings.static_dir), html=True), name="miniapp")


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
