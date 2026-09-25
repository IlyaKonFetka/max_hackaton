"""Отправка сообщений пользователю из любого места (API, планировщик) через общий экземпляр бота."""

from __future__ import annotations

import logging

from maxapi.types import InputMedia

from ..bot.client import bot
from ..config import settings
from ..db import CheckSession, User, Venue, db_session
from .pdf import build_act
from .sessions import local_date, open_tasks

log = logging.getLogger(__name__)


async def send_text(user: User, text: str, attachments=None) -> bool:
    try:
        if user.chat_id:
            await bot.send_message(chat_id=user.chat_id, text=text, attachments=attachments)
        else:
            await bot.send_message(user_id=user.id, text=text, attachments=attachments)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось отправить сообщение user=%s: %s", user.id, e)
        return False


async def send_act(session_id: int) -> bool:
    """Собирает PDF-акт и отправляет владельцу в чат вместе с планом устранения."""
    with db_session() as db:
        session = db.get(CheckSession, session_id)
        if session is None:
            return False
        venue = db.get(Venue, session.venue_id)
        owner = db.get(User, venue.owner_id)
        if owner is None or not owner.chat_id:
            # Пользователь без диалога с ботом (тестовый доступ через API) — PDF собираем, но слать некуда.
            build_act(session, venue, "Эксперт (тестовый доступ)")
            return False
        owner_name = f"{owner.first_name} {owner.last_name}".strip() or f"id {owner.id}"
        path = build_act(session, venue, owner_name)
        session.act_path = str(path)
        tasks = open_tasks(db, owner.id, venue.id)
        tz = settings.timezone_offset_hours
        from .sessions import session_report

        counts = session_report(session)["counts"]

    lines = [
        f"Акт самопроверки «{venue.name}» готов.",
        f"Соблюдается: {counts['ok']} · Нарушений: {counts['violation']} · Не знаю: {counts['unknown']}",
    ]
    if tasks:
        lines.append("")
        lines.append(f"План устранения — {len(tasks)} задач:")
        for t in tasks[:10]:
            lines.append(f"• до {local_date(t.due_date, tz)} — {t.title}")
        if len(tasks) > 10:
            lines.append(f"…и ещё {len(tasks) - 10}")
        lines.append("")
        lines.append("Назначить ответственных и закрывать задачи — /tasks. Я напомню о сроках.")
    else:
        lines.append("")
        lines.append("Нарушений нет — задач в плане устранения не создано.")

    ok = await send_text(owner, "\n".join(lines))
    try:
        await bot.send_message(
            chat_id=owner.chat_id, user_id=None if owner.chat_id else owner.id,
            attachments=[InputMedia(str(path))],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось отправить PDF: %s", e)
        await send_text(owner, "PDF не удалось отправить как файл. Попробуйте команду /act ещё раз.")
        return False
    return ok
