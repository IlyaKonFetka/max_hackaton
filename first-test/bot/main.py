"""
Тестовый бот для проверки связки: токен → Bot API → кнопки → мини-приложение.

Запуск:  python main.py
Токен берётся из переменной окружения MAX_BOT_TOKEN,
либо (для локальной разработки) из ../../THEORY/token.txt — папка THEORY в .gitignore.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Кириллица в консоли Windows.
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from maxapi import Bot, Dispatcher
from maxapi.filters import F
from maxapi.types import (
    BotStarted,
    CallbackButton,
    Command,
    CommandStart,
    LinkButton,
    MessageCallback,
    MessageCreated,
    OpenAppButton,
    RequestContactButton,
    RequestGeoLocationButton,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("first-test")

# Адрес тестовой страницы мини-приложения на GitHub Pages.
MINIAPP_URL = "https://ilyakonfetka.github.io/max_hackaton/"


def load_token() -> str:
    token = os.environ.get("MAX_BOT_TOKEN")
    if token:
        return token.strip()
    fallback = Path(__file__).resolve().parents[2] / "THEORY" / "token.txt"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8").strip()
    raise SystemExit("Нет токена: задайте MAX_BOT_TOKEN или положите THEORY/token.txt")


bot = Bot(load_token())
dp = Dispatcher()


def main_keyboard(bot_username: str, bot_id: int):
    kb = InlineKeyboardBuilder()
    # open_app открывает мини-приложение, ПРИВЯЗАННОЕ к боту на Платформе для бизнеса.
    # URL сюда передать нельзя — пока организаторы не привяжут ссылку, кнопка ничего не откроет.
    kb.row(OpenAppButton(text="Открыть мини-приложение", web_app=bot_username, contact_id=bot_id))
    # Запасной путь: обычная ссылка — откроется во встроенном браузере, но без контекста Bridge.
    kb.row(LinkButton(text="Открыть как ссылку (без Bridge)", url=MINIAPP_URL))
    kb.row(
        CallbackButton(text="Ping", payload="ping"),
        RequestContactButton(text="Контакт"),
        RequestGeoLocationButton(text="Гео"),
    )
    return kb.as_markup()


async def send_hello(chat_id: int):
    me = bot.me
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "Тестовый бот «Смена без штрафа».\n\n"
            "Проверяем: команды, кнопки, callback, контакт, геолокацию и открытие мини-приложения.\n"
            "Напиши любой текст — отвечу эхом."
        ),
        attachments=[main_keyboard(me.username, me.user_id)],
    )


@dp.bot_started()
async def on_bot_started(event: BotStarted):
    log.info("bot_started from user=%s chat=%s", getattr(event, "user", None), event.chat_id)
    await send_hello(event.chat_id)


@dp.message_created(CommandStart())
async def on_start(event: MessageCreated):
    await send_hello(event.message.recipient.chat_id)


@dp.message_created(Command("id"))
async def on_id(event: MessageCreated):
    m = event.message
    await m.answer(
        f"chat_id: {m.recipient.chat_id}\n"
        f"user_id: {m.sender.user_id}\n"
        f"name: {m.sender.first_name} {m.sender.last_name or ''}\n"
        f"username: {m.sender.username}"
    )


@dp.message_callback()
async def on_callback(cb: MessageCallback):
    log.info("callback payload=%s", cb.callback.payload)
    await cb.answer(new_text=None, notification=f"Получил callback: {cb.callback.payload}")
    await cb.message.answer(f"Callback работает: {cb.callback.payload}")


@dp.message_created(F.message.body.attachments)
async def on_attachments(event: MessageCreated):
    # Сюда приходят контакт, геолокация, фото и любые другие вложения.
    kinds = [a.type for a in event.message.body.attachments]
    log.info("attachments: %s", kinds)
    await event.message.answer(f"Получил вложения: {', '.join(str(k) for k in kinds)}")


@dp.message_created(F.message.body.text)
async def on_text(event: MessageCreated):
    await event.message.answer(f"Эхо: {event.message.body.text}")


async def main():
    me = await bot.get_me()
    log.info("Запущен как @%s (id=%s)", me.username, me.user_id)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
