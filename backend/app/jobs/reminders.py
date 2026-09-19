"""Напоминания: дедлайны задач (за день и в день срока, просрочка) и утренний чек-лист смены."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from ..config import settings
from ..db import Task, User, Venue, db_session, utcnow
from ..services.notify import send_text
from ..services.sessions import local_date

log = logging.getLogger(__name__)

SHIFT_REMINDER_HOUR = 9  # локальное время
_last_shift_reminder_date: str | None = None


async def _remind_tasks() -> None:
    now = utcnow()
    tz = settings.timezone_offset_hours
    with db_session() as db:
        tasks = list(db.execute(select(Task).where(Task.status == "open")).scalars().all())
        to_send: list[tuple[User, str]] = []
        for t in tasks:
            recipients = [db.get(User, t.owner_id)]
            if t.assignee_user_id and t.assignee_user_id != t.owner_id:
                a = db.get(User, t.assignee_user_id)
                if a:
                    recipients.append(a)
            due_local = local_date(t.due_date, tz)
            hours_left = (t.due_date - now).total_seconds() / 3600
            already = t.last_reminded_at and (now - t.last_reminded_at) < timedelta(hours=20)

            if hours_left < 0 and not t.overdue_notified:
                t.overdue_notified = True
                t.last_reminded_at = now
                for u in recipients:
                    if u:
                        to_send.append((u, f"⚠ Просрочена задача (срок {due_local}):\n{t.title}\n\nЗакрыть: /tasks"))
            elif 0 <= hours_left <= 30 and not already:
                t.last_reminded_at = now
                for u in recipients:
                    if u:
                        to_send.append((u, f"Напоминание: до {due_local} нужно закрыть задачу:\n{t.title}\n\nОткрыть: /tasks"))
        db.flush()
    for u, text in to_send:
        await send_text(u, text)


async def _remind_shift() -> None:
    global _last_shift_reminder_date
    local = utcnow() + timedelta(hours=settings.timezone_offset_hours)
    today = local.strftime("%Y-%m-%d")
    if local.hour != SHIFT_REMINDER_HOUR or _last_shift_reminder_date == today:
        return
    _last_shift_reminder_date = today
    with db_session() as db:
        venues = list(db.execute(select(Venue)).scalars().all())
        owners = [db.get(User, v.owner_id) for v in venues]
    for u in owners:
        if u:
            await send_text(u, "Доброе утро. Чек-лист смены на сегодня — /shift")


async def reminder_loop() -> None:
    log.info("планировщик напоминаний запущен (каждые %s с)", settings.reminder_interval_sec)
    while True:
        try:
            await _remind_tasks()
            await _remind_shift()
        except Exception as e:  # noqa: BLE001
            log.exception("планировщик: %s", e)
        await asyncio.sleep(settings.reminder_interval_sec)
