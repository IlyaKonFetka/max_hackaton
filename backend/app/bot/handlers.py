"""Сценарий бота: онбординг-профиль, результат расчёта, статус, задачи, чек-лист смены.

Состояние диалога хранится в БД (BotState), поэтому перезапуск процесса не роняет сценарий.
Все callback'и идут в один диспетчер по префиксу payload — меньше магии, легче отлаживать.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from maxapi.enums.attachment import AttachmentType
from maxapi.types import BotStarted, MessageCallback, MessageCreated
from sqlalchemy import select

from ..config import settings
from ..db import BotState, CheckSession, Membership, ShiftCheck, ShiftItem, ShiftPhoto, Task, User, Venue, db_session, utcnow
from ..engine import summary
from ..rulebook import get_rulebook
from ..services import sessions as svc
from ..services.media import download_from_max
from . import keyboards as kb
from . import texts
from .client import bot, dp

log = logging.getLogger(__name__)


# ---------- утилиты состояния ----------

def _state(db, user_id: int) -> BotState:
    st = db.get(BotState, user_id)
    if st is None:
        st = BotState(user_id=user_id, state="idle", data={})
        db.add(st)
        db.flush()
    return st


def _set_state(db, user_id: int, state: str, data: dict | None = None) -> None:
    st = _state(db, user_id)
    st.state = state
    if data is not None:
        st.data = data
    db.flush()


def _user_from_event(db, user, chat_id: int | None) -> User:
    return svc.get_or_create_user(
        db, user.user_id, first_name=user.first_name or "", last_name=user.last_name or "",
        username=user.username, chat_id=chat_id,
    )


async def _send(chat_id: int, text: str, attachments=None):
    return await bot.send_message(chat_id=chat_id, text=text, attachments=attachments)


async def _replace(cb: MessageCallback, text: str | None, attachments: list, notification: str | None = None):
    """Ответ на callback, который заменяет исходное сообщение (текст + клавиатура) — так это делается в MAX.

    attachments=[] убирает клавиатуру. Если платформа отказала — пробуем edit_message, но callback подтверждаем всегда.
    """
    try:
        await cb.answer(new_text=text, attachments=attachments, notification=notification)
    except Exception as e:  # noqa: BLE001
        log.warning("answer/replace не удался (%s), пробую edit_message", e)
        try:
            await bot.edit_message(message_id=cb.message.body.mid, text=text, attachments=attachments)
        except Exception as e2:  # noqa: BLE001
            log.warning("edit_message тоже не удался: %s", e2)
        try:
            await cb.ack(notification=notification)
        except Exception:  # noqa: BLE001
            pass


def _local_now() -> datetime:
    return utcnow() + timedelta(hours=settings.timezone_offset_hours)


def _fmt_date(dt: datetime) -> str:
    return svc.local_date(dt, settings.timezone_offset_hours)


# ---------- онбординг ----------

def _fields():
    return list(get_rulebook().fields)


async def start_onboarding(chat_id: int, user_id: int, intro: bool = True):
    with db_session() as db:
        _set_state(db, user_id, f"onb:{_fields()[0].key}", {"profile": {}})
    if intro:
        await _send(chat_id, texts.INTRO)
    await _ask(chat_id, _fields()[0].key)


async def _ask(chat_id: int, field_key: str):
    field = get_rulebook().field(field_key)
    if field.type == "text":
        await _send(chat_id, field.question, attachments=[kb.text_question_kb(field)] if kb.text_question_kb(field) else None)
    else:
        await _send(chat_id, field.question, attachments=[kb.question_kb(field)])


async def _advance(chat_id: int, user_id: int, field_key: str, value):
    """Сохраняет ответ и задаёт следующий вопрос или показывает сводку профиля."""
    fields = _fields()
    keys = [f.key for f in fields]
    idx = keys.index(field_key)
    with db_session() as db:
        st = _state(db, user_id)
        data = dict(st.data or {})
        profile = dict(data.get("profile") or {})
        profile[field_key] = value
        data["profile"] = profile
        if idx + 1 < len(fields):
            _set_state(db, user_id, f"onb:{keys[idx + 1]}", data)
            next_key = keys[idx + 1]
        else:
            _set_state(db, user_id, "onb:confirm", data)
            next_key = None

    if next_key:
        await _ask(chat_id, next_key)
    else:
        await _show_profile_summary(chat_id, profile)


async def _show_profile_summary(chat_id: int, profile: dict):
    from ..engine import profile_summary_lines

    lines = profile_summary_lines(profile, get_rulebook())
    await _send(chat_id, "Проверьте профиль:\n\n" + "\n".join(f"• {ln}" for ln in lines), attachments=[kb.confirm_profile_kb()])


async def _confirm_profile(chat_id: int, user_id: int):
    with db_session() as db:
        st = _state(db, user_id)
        profile = dict((st.data or {}).get("profile") or {})
        if not profile:
            _set_state(db, user_id, "idle", {})
            await _send(chat_id, "Профиль пуст. Начнём заново: /start")
            return
        venue = svc.save_venue(db, user_id, profile)
        # Старая незавершённая сессия закрывается: профиль мог измениться.
        old = svc.open_session(db, venue)
        if old:
            old.finished_at = utcnow()
        # Сегодняшний чек-лист смены собран под старый профиль — удаляем, /shift соберёт заново.
        today = _local_now().strftime("%Y-%m-%d")
        for sc in db.execute(select(ShiftCheck).where(ShiftCheck.venue_id == venue.id, ShiftCheck.date == today)).scalars().all():
            db.delete(sc)
        db.flush()
        session = svc.start_session(db, venue)
        _set_state(db, user_id, "idle", {})
        summ = summary(venue.profile, get_rulebook())
        venue_name, session_id = venue.name, session.id
    await _send(chat_id, texts.result_text(venue_name, summ), attachments=[kb.result_kb(session_id, summ["not_applicable"])])


# ---------- статус, задачи ----------

async def show_status(chat_id: int, user_id: int):
    with db_session() as db:
        venue = svc.current_venue(db, user_id)
        if venue is None:
            await start_onboarding(chat_id, user_id)
            return
        open_s = svc.open_session(db, venue)
        last = svc.latest_session(db, venue)
        tasks = svc.open_tasks(db, user_id, venue.id)
        lines = [f"{venue.name}" + (f", {venue.region}" if venue.region else "")]
        if open_s:
            p = svc.progress(open_s)
            lines.append(f"Самопроверка в процессе: {p['answered']} из {p['total']} пунктов.")
        elif last and last.finished_at:
            c = svc.session_report(last)["counts"]
            lines.append(
                f"Последняя самопроверка {_fmt_date(last.finished_at)}: соблюдается {c['ok']}, нарушений {c['violation']}, не знаю {c['unknown']}."
            )
        else:
            lines.append("Самопроверка ещё не проводилась.")
        if tasks:
            overdue = [t for t in tasks if t.due_date < utcnow()]
            lines.append(f"Открытых задач: {len(tasks)}" + (f", просрочено: {len(overdue)}" if overdue else "") +
                         f". Ближайший срок: {_fmt_date(tasks[0].due_date)} — {tasks[0].title}")
        else:
            lines.append("Открытых задач нет.")
        today = _local_now().strftime("%Y-%m-%d")
        sc = db.execute(select(ShiftCheck).where(ShiftCheck.venue_id == venue.id, ShiftCheck.date == today)).scalars().first()
        if sc:
            items = db.execute(select(ShiftItem).where(ShiftItem.shift_check_id == sc.id)).scalars().all()
            done = sum(1 for it in items if it.status == "ok")
            lines.append(f"Чек-лист смены на сегодня: {done} из {len(items)}.")
        else:
            lines.append("Чек-лист смены на сегодня не заполнен.")
        markup = kb.status_kb(bool(open_s), open_s.id if open_s else None, len(tasks))
    await _send(chat_id, "\n".join(lines), attachments=[markup])


async def show_tasks(chat_id: int, user_id: int):
    with db_session() as db:
        venue, m = svc.role_of(db, user_id)
        if m is not None and m.role == "staff":
            tasks = svc.tasks_for_assignee(db, user_id)
            staff = True
        else:
            tasks = svc.open_tasks(db, user_id)
            staff = False
        if not tasks:
            await _send(chat_id, "Открытых задач нет.")
            return
        await _send(chat_id, f"{'Мои задачи' if staff else 'Открытые задачи'} — {len(tasks)}:")
        for t in tasks[:15]:
            overdue = t.due_date < utcnow()
            who = "" if staff else (f"\nОтветственный: {t.assignee_name}" if t.assignee_name else "")
            text = f"{'⚠ Просрочено' if overdue else 'До'} {_fmt_date(t.due_date)}\n{t.title}{who}"
            markup = kb.staff_task_kb(t.id) if staff else kb.task_kb(t.id, bool(t.assignee_name))
            await _send(chat_id, text, attachments=[markup])


async def _show_task_for_assignee(chat_id: int, task: Task):
    await _send(
        chat_id,
        f"Вам назначена задача:\n{task.title}\nСрок: {_fmt_date(task.due_date)}\n\n"
        "Когда сделаете — нажмите кнопку и пришлите фото результата: оно попадёт в акт.",
        attachments=[kb.staff_task_kb(task.id)],
    )


async def show_staff_home(chat_id: int, user_id: int, venue: Venue):
    with db_session() as db:
        tasks = svc.tasks_for_assignee(db, user_id)
        n = len(tasks)
        nearest = tasks[0] if tasks else None
        due = _fmt_date(nearest.due_date) if nearest else ""
    lines = [f"{venue.name} — вы сотрудник."]
    if n:
        lines.append(f"Открытых задач: {n}. Ближайший срок: {due} — {nearest.title}")
    else:
        lines.append("Открытых задач нет.")
    lines.append("Чек-лист смены — по одному пункту, фото засчитывается как «выполнено».")
    await _send(chat_id, "\n".join(lines), attachments=[kb.staff_home_kb(n)])


async def _is_staff(user_id: int) -> bool:
    with db_session() as db:
        _v, m = svc.role_of(db, user_id)
    return m is not None and m.role == "staff"


async def _owner_only(chat_id: int, user_id: int) -> Venue | None:
    """Заведение, если пользователь — владелец; иначе сообщение и None."""
    with db_session() as db:
        venue = svc.current_venue(db, user_id)
    if venue is None:
        await _send(chat_id, texts.OWNER_ONLY)
    return venue


# ---------- команда ----------

async def show_team(chat_id: int, user_id: int):
    venue = await _owner_only(chat_id, user_id)
    if venue is None:
        return
    with db_session() as db:
        staff = svc.staff_of(db, venue.id)
        rows = []
        for m in staff:
            state = "в MAX" if m.joined_at else "приглашён, ещё не открыл бота"
            phone = f" · ···{m.phone[-4:]}" if m.phone else ""
            rows.append(f"• {m.name or 'Сотрудник'}{phone} — {state}")
    text = f"{venue.name}\nВладелец: вы.\n"
    text += ("Сотрудники:\n" + "\n".join(rows)) if rows else "Сотрудников пока нет."
    text += "\n\nСотрудник видит свои задачи и чек-лист смены; назначать и менять профиль может только владелец."
    await _send(chat_id, text, attachments=[kb.team_kb()])


async def _invite_from_contact(chat_id: int, user_id: int, attachment):
    from urllib.parse import quote

    payload = getattr(attachment, "payload", None)
    name, phone, max_uid, username = "", "", None, None
    try:
        name = payload.vcf.full_name or ""
        phone = payload.vcf.phone or ""
    except Exception:  # noqa: BLE001
        pass
    mi = getattr(payload, "max_info", None)
    if mi is not None:
        max_uid = getattr(mi, "user_id", None)
        username = getattr(mi, "username", None)
        if not name:
            name = f"{getattr(mi, 'first_name', '')} {getattr(mi, 'last_name', '') or ''}".strip()
    with db_session() as db:
        _set_state(db, user_id, "idle", {})
        venue = svc.current_venue(db, user_id)
        if venue is None:
            await _send(chat_id, texts.OWNER_ONLY)
            return
        if max_uid and max_uid == user_id:
            await _send(chat_id, "Это ваш собственный контакт — выберите сотрудника.")
            return
        m = svc.invite_staff(db, venue, user_id, name, phone, max_uid)
        mid, venue_name = m.id, venue.name
        joined = m.joined_at is not None
        assignee_user = db.get(User, max_uid) if max_uid else None
    if joined:
        await _send(chat_id, f"{name or 'Сотрудник'} уже в команде.")
        return
    invite = (f"Вас добавили в команду «{venue_name}» в MAX. Здесь будут ваши задачи и чек-лист смены.\n"
              f"Откройте, чтобы присоединиться: https://max.ru/{settings.bot_username}?start=join_{mid}")
    if assignee_user and assignee_user.chat_id:
        try:
            await _send(assignee_user.chat_id, invite)
            await _send(chat_id, f"{name or 'Сотрудник'} получил приглашение в MAX.")
            return
        except Exception:  # noqa: BLE001
            pass
    share_url = f"https://max.ru/:share?text={quote(invite, safe='')}"
    profile_url = f"https://max.ru/{username}" if username else None
    await _send(chat_id, f"{name or 'Сотрудник'} записан. Нажмите кнопку — откроется отправка в MAX с готовым приглашением.",
                attachments=[kb.assign_result_kb(share_url, profile_url)])


async def _join_team(chat_id: int, user_id: int, raw_id: str):
    """Сотрудник открыл ссылку-приглашение: сверяем аккаунт или просим подтвердить номер."""
    try:
        mid = int(raw_id)
    except ValueError:
        await show_status_or_onboarding(chat_id, user_id)
        return
    with db_session() as db:
        m = db.get(Membership, mid)
        venue = db.get(Venue, m.venue_id) if m else None
        if m is None or venue is None:
            await _send(chat_id, "Приглашение не найдено.")
            return
        if venue.owner_id == user_id:
            await _send(chat_id, "Это ссылка для сотрудника — перешлите её ему.")
            return
        if m.user_id and m.user_id != user_id:
            await _send(chat_id, "Это приглашение для другого человека.")
            return
        if not m.user_id and m.phone:
            _set_state(db, user_id, f"join:{mid}", {})
            await _send(chat_id, f"Приглашение в «{venue.name}» выписано на номер ···{m.phone[-4:]}. Подтвердите — отправьте свой контакт.",
                        attachments=[kb.claim_kb()])
            return
        u = db.get(User, user_id)
        svc.join_staff(db, m, user_id, f"{u.first_name} {u.last_name}".strip() if u else "")
        owner = db.get(User, venue.owner_id)
        name = m.name
    await _send(chat_id, f"Вы в команде «{venue.name}».")
    await show_staff_home(chat_id, user_id, venue)
    if owner and owner.chat_id:
        await _send(owner.chat_id, f"{name or 'Сотрудник'} присоединился к команде.")


async def _join_with_contact(chat_id: int, user_id: int, mid: int, attachment):
    payload = getattr(attachment, "payload", None)
    phone = ""
    try:
        phone = payload.vcf.phone or ""
    except Exception:  # noqa: BLE001
        pass
    mi = getattr(payload, "max_info", None)
    contact_uid = getattr(mi, "user_id", None) if mi is not None else None
    with db_session() as db:
        _set_state(db, user_id, "idle", {})
        m = db.get(Membership, mid)
        venue = db.get(Venue, m.venue_id) if m else None
        if m is None or venue is None:
            await _send(chat_id, "Приглашение не найдено.")
            return
        own_contact = contact_uid is None or contact_uid == user_id
        if not own_contact or svc.norm_phone(phone) != m.phone:
            await _send(chat_id, "Номер не совпадает с приглашением. Попросите владельца пригласить вас заново.")
            return
        u = db.get(User, user_id)
        svc.join_staff(db, m, user_id, f"{u.first_name} {u.last_name}".strip() if u else "")
        owner = db.get(User, venue.owner_id)
        name = m.name
    await _send(chat_id, f"Вы в команде «{venue.name}».")
    await show_staff_home(chat_id, user_id, venue)
    if owner and owner.chat_id:
        await _send(owner.chat_id, f"{name or 'Сотрудник'} присоединился к команде.")


# ---------- чек-лист смены (живёт в боте) ----------
#
# Пошаговый режим: один пункт за раз, полный текст — в теле сообщения, кнопки короткие.
# Так нет ни обрезанных подписей на кнопках, ни потерянных нажатий: клиент MAX глотает тап,
# пока предыдущее обновление сообщения в полёте, поэтому тумблеры в одной клавиатуре ненадёжны.
# Фото, присланное во время вопроса, привязывается именно к этому пункту.

SHIFT_ANSWER_LABEL = {"ok": "✅ выполнено", "no": "❌ не выполнено", "skip": "пропущено"}


def _shift_rules(venue: Venue):
    from ..engine import applicable

    return [r for r in applicable(venue.profile, get_rulebook()) if r.period == "shift"]


def _get_or_create_shift(db, venue: Venue, user_id: int, rules: list) -> ShiftCheck:
    """Сегодняшний чек-лист смены; по строке ShiftItem на каждое применимое требование."""
    today = _local_now().strftime("%Y-%m-%d")
    sc = db.execute(select(ShiftCheck).where(ShiftCheck.venue_id == venue.id, ShiftCheck.date == today)).scalars().first()
    if sc is None:
        sc = ShiftCheck(venue_id=venue.id, user_id=user_id, date=today)
        db.add(sc)
        db.flush()
    wanted = {r.id for r in rules}
    existing_items = db.execute(select(ShiftItem).where(ShiftItem.shift_check_id == sc.id)).scalars().all()
    existing = {it.rule_id for it in existing_items}
    for it in existing_items:
        if it.rule_id not in wanted:
            db.delete(it)
    for r in rules:
        if r.id not in existing:
            db.add(ShiftItem(shift_check_id=sc.id, rule_id=r.id, status=None))
    db.flush()
    return sc


def _shift_items(db, check_id: int) -> dict[str, ShiftItem]:
    return {it.rule_id: it for it in db.execute(select(ShiftItem).where(ShiftItem.shift_check_id == check_id)).scalars().all()}


def _next_unanswered(rules: list, items: dict[str, ShiftItem], skipped: list[str], left: list[str] = ()) -> str | None:
    """Первый неотвеченный пункт; пропущенные — в конец; пропущенные дважды (left) больше не спрашиваем."""
    def open_(rid: str) -> bool:
        return rid not in left and (items.get(rid) is None or items[rid].status is None)
    for r in rules:
        if r.id not in skipped and open_(r.id):
            return r.id
    for rid in skipped:
        if open_(rid):
            return rid
    return None


async def show_shift(chat_id: int, user_id: int):
    with db_session() as db:
        venue, _m = svc.role_of(db, user_id)
        if venue is None:
            await start_onboarding(chat_id, user_id)
            return
        rules = _shift_rules(venue)
        sc = _get_or_create_shift(db, venue, user_id, rules)
        items = _shift_items(db, sc.id)
        answered = sum(1 for r in rules if items.get(r.id) and items[r.id].status)
        check_id, total = sc.id, len(rules)
        d = _local_now().strftime("%d.%m")
    if answered >= total:
        await _shift_summary(chat_id, user_id, check_id)
        return
    with db_session() as db:
        _set_state(db, user_id, f"shift:{check_id}", {"skipped": []})
    if answered == 0:
        await _send(chat_id, f"Чек-лист смены {d} — {total} пунктов. По одному: нажмите кнопку или пришлите фото вместо неё.")
    else:
        await _send(chat_id, f"Продолжаем чек-лист смены {d}: отмечено {answered} из {total}.")
    await _ask_shift_item(chat_id, user_id, check_id)


async def _ask_shift_item(chat_id: int, user_id: int, check_id: int):
    with db_session() as db:
        sc = db.get(ShiftCheck, check_id)
        if sc is None:
            _set_state(db, user_id, "idle", {})
            return
        venue = db.get(Venue, sc.venue_id)
        rules = _shift_rules(venue)
        items = _shift_items(db, check_id)
        st = _state(db, user_id)
        skipped = list((st.data or {}).get("skipped") or [])
        left = list((st.data or {}).get("left") or [])
        rid = _next_unanswered(rules, items, skipped, left)
        if rid is None:
            _set_state(db, user_id, "idle", {})
        else:
            _set_state(db, user_id, f"shift:{check_id}", {"skipped": skipped, "left": left, "current": rid})
        by_id = {r.id: r for r in rules}
        n = sum(1 for r in rules if items.get(r.id) and items[r.id].status) + 1
        total = len(rules)
    if rid is None:
        await _shift_summary(chat_id, user_id, check_id)
        return
    r = by_id[rid]
    text = f"{n}/{total} · {r.agency_label}\n\n{r.title}"
    if r.check:
        text += f"\n\nЧто проверить: {r.check}"
    await _send(chat_id, text, attachments=[kb.shift_item_kb(check_id, rid)])


async def _answer_shift_item(cb: MessageCallback, check_id: int, rule_id: str, answer: str):
    user_id = cb.callback.user.user_id
    chat_id = cb.message.recipient.chat_id
    rules_by_id = svc.rules_by_id()
    title = rules_by_id[rule_id].title if rule_id in rules_by_id else rule_id
    with db_session() as db:
        item = db.execute(
            select(ShiftItem).where(ShiftItem.shift_check_id == check_id, ShiftItem.rule_id == rule_id)
        ).scalars().first()
        if item is None:
            await cb.ack(notification="Пункт не найден")
            return
        if item.status is not None:
            # Повторный тап по уже отвеченному вопросу (двойное нажатие) — ничего не меняем и не спрашиваем дальше.
            await cb.ack(notification="Уже отмечено")
            return
        st = _state(db, user_id)
        data = dict(st.data or {})
        skipped = list(data.get("skipped") or [])
        left = list(data.get("left") or [])
        if answer == "skip":
            if rule_id in skipped:
                left.append(rule_id)  # второй пропуск — оставляем неотмеченным и идём дальше
            else:
                skipped.append(rule_id)
        else:
            item.status = answer
        db.flush()
        _set_state(db, user_id, f"shift:{check_id}", {"skipped": skipped, "left": left})
    await _replace(cb, f"{title}\n— {SHIFT_ANSWER_LABEL[answer]}", [], notification=SHIFT_ANSWER_LABEL[answer])
    await _ask_shift_item(chat_id, user_id, check_id)


async def _shift_photo_answer(chat_id: int, user_id: int, check_id: int, url: str):
    """Фото во время вопроса = «выполнено» с доказательством, привязанным к этому пункту."""
    with db_session() as db:
        st = _state(db, user_id)
        rid = (st.data or {}).get("current")
    if not rid:
        await _attach_shift_photo(chat_id, user_id, url)
        return
    try:
        rel = await download_from_max(url)
    except Exception as e:  # noqa: BLE001
        log.warning("фото не скачалось: %s", e)
        await _send(chat_id, "Не удалось сохранить фото, попробуйте ещё раз или нажмите кнопку.")
        return
    with db_session() as db:
        item = db.execute(
            select(ShiftItem).where(ShiftItem.shift_check_id == check_id, ShiftItem.rule_id == rid)
        ).scalars().first()
        if item is not None and item.status is None:
            item.status = "ok"
            item.photo_path = rel
            db.flush()
    rule = svc.rules_by_id().get(rid)
    await _send(chat_id, f"Фото принято: «{rule.title if rule else rid}» — ✅ выполнено.")
    await _ask_shift_item(chat_id, user_id, check_id)


async def _shift_summary(chat_id: int, user_id: int, check_id: int, cb: MessageCallback | None = None):
    with db_session() as db:
        sc = db.get(ShiftCheck, check_id)
        if sc is None:
            await _send(chat_id, "Чек-лист не найден.")
            return
        venue = db.get(Venue, sc.venue_id)
        rules = _shift_rules(venue)
        items = _shift_items(db, check_id)
        _set_state(db, user_id, "idle", {})
        ok = [r.title for r in rules if items.get(r.id) and items[r.id].status == "ok"]
        no = [r.title for r in rules if items.get(r.id) and items[r.id].status == "no"]
        left = [r.title for r in rules if not items.get(r.id) or items[r.id].status is None]
        total = len(rules)
        date = sc.date
    lines = [f"Смена {date[8:10]}.{date[5:7]}: выполнено {len(ok)} из {total}."]
    if no:
        lines.append("\nНе выполнено:\n" + "\n".join(f"• {t}" for t in no))
    if left:
        lines.append("\nНе отмечено:\n" + "\n".join(f"• {t}" for t in left))
    if no or left:
        lines.append("\nЭти пункты стоит закрыть до конца смены — они попадут в следующую самопроверку как требующие внимания.")
    else:
        lines.append("Все пункты закрыты. Хорошего дня.")
    text = "\n".join(lines)
    if cb is not None:
        await _replace(cb, text, [kb.shift_summary_kb(check_id)], notification="Смена закрыта")
    else:
        await _send(chat_id, text, attachments=[kb.shift_summary_kb(check_id)])


async def _restart_shift(cb: MessageCallback, check_id: int):
    user_id = cb.callback.user.user_id
    chat_id = cb.message.recipient.chat_id
    with db_session() as db:
        for it in _shift_items(db, check_id).values():
            it.status = None
            it.photo_path = None
        db.flush()
        _set_state(db, user_id, f"shift:{check_id}", {"skipped": [], "left": []})
    await cb.ack(notification="Заново")
    await _ask_shift_item(chat_id, user_id, check_id)


# ---------- события ----------

@dp.bot_started()
async def on_bot_started(event: BotStarted):
    with db_session() as db:
        _user_from_event(db, event.user, event.chat_id)
    payload = (event.payload or "").strip()
    if payload.startswith("task_"):
        await _claim_task(event.chat_id, event.user.user_id, payload[5:])
        return
    if payload.startswith("join_"):
        await _join_team(event.chat_id, event.user.user_id, payload[5:])
        return
    await show_status_or_onboarding(event.chat_id, event.user.user_id)


async def show_status_or_onboarding(chat_id: int, user_id: int):
    with db_session() as db:
        venue, m = svc.role_of(db, user_id)
    if venue is None:
        await start_onboarding(chat_id, user_id)
    elif m is not None and m.role == "staff":
        await show_staff_home(chat_id, user_id, venue)
    else:
        await show_status(chat_id, user_id)


async def _claim_task(chat_id: int, user_id: int, raw_id: str):
    try:
        task_id = int(raw_id)
    except ValueError:
        await show_status_or_onboarding(chat_id, user_id)
        return
    with db_session() as db:
        task = db.get(Task, task_id)
        if task is None or task.status != "open":
            await _send(chat_id, "Эта задача уже закрыта или не найдена.")
            return
        if task.owner_id == user_id:
            await _send(chat_id, "Это ссылка для сотрудника — у вас задача и так есть в /tasks. Перешлите ссылку ему.")
            return
        # Назначен конкретный аккаунт MAX — принять может только он.
        if task.assignee_user_id and task.assignee_user_id != user_id:
            await _send(chat_id, "Эта задача назначена другому сотруднику.")
            return
        # Аккаунт неизвестен, но известен телефон — просим подтвердить номер.
        if not task.assignee_user_id and task.assignee_phone:
            _set_state(db, user_id, f"claim:{task_id}", {})
            await _send(
                chat_id,
                f"Задача «{task.title}» назначена на номер ···{_norm_phone(task.assignee_phone)[-4:]}. "
                "Подтвердите, что это вы — отправьте свой контакт.",
                attachments=[kb.claim_kb()],
            )
            return
        _do_claim(db, task, user_id)
        owner = db.get(User, task.owner_id)
        u = db.get(User, user_id)
        name = f"{u.first_name} {u.last_name}".strip()
    await _show_task_for_assignee(chat_id, task)
    if owner and owner.chat_id:
        await _send(owner.chat_id, f"{name} принял(а) задачу: {task.title}")


def _do_claim(db, task: Task, user_id: int) -> None:
    task.assignee_user_id = user_id
    u = db.get(User, user_id)
    name = f"{u.first_name} {u.last_name}".strip() if u else ""
    if not task.assignee_name:
        task.assignee_name = name
    venue = db.get(Venue, task.venue_id)
    if venue is not None:
        m = svc.find_staff(db, venue.id, user_id=user_id, phone=task.assignee_phone)
        if m is None:
            m = svc.invite_staff(db, venue, task.owner_id, task.assignee_name, task.assignee_phone, user_id)
        svc.join_staff(db, m, user_id, name)
    db.flush()


async def _claim_with_contact(chat_id: int, user_id: int, task_id: int, attachment):
    """Сотрудник прислал свой контакт по просьбе бота — сверяем телефон с назначенным."""
    payload = getattr(attachment, "payload", None)
    phone = ""
    try:
        phone = payload.vcf.phone or ""
    except Exception:  # noqa: BLE001
        pass
    mi = getattr(payload, "max_info", None)
    contact_uid = getattr(mi, "user_id", None) if mi is not None else None
    with db_session() as db:
        _set_state(db, user_id, "idle", {})
        task = db.get(Task, task_id)
        if task is None or task.status != "open":
            await _send(chat_id, "Задача уже закрыта или не найдена.")
            return
        own_contact = contact_uid is None or contact_uid == user_id
        if not own_contact or _norm_phone(phone) != _norm_phone(task.assignee_phone):
            await _send(chat_id, "Номер не совпадает с назначенным. Если это ошибка — попросите владельца переназначить задачу.")
            return
        _do_claim(db, task, user_id)
        owner = db.get(User, task.owner_id)
        u = db.get(User, user_id)
        name = f"{u.first_name} {u.last_name}".strip()
    await _show_task_for_assignee(chat_id, task)
    if owner and owner.chat_id:
        await _send(owner.chat_id, f"{name} принял(а) задачу: {task.title}")


@dp.message_created()
async def on_message(event: MessageCreated):
    m = event.message
    chat_id = m.recipient.chat_id
    user_id = m.sender.user_id
    text = (m.body.text or "").strip()
    attachments = list(m.body.attachments or [])

    with db_session() as db:
        _user_from_event(db, m.sender, chat_id)
        st = _state(db, user_id)
        state, data = st.state, dict(st.data or {})

    # Команды
    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        with db_session() as db:
            _set_state(db, user_id, "idle", {})
        if cmd == "/start":
            await show_status_or_onboarding(chat_id, user_id)
        elif cmd == "/profile":
            if await _is_staff(user_id):
                await _send(chat_id, texts.OWNER_ONLY)
            else:
                await start_onboarding(chat_id, user_id, intro=False)
        elif cmd == "/status":
            await show_status_or_onboarding(chat_id, user_id)
        elif cmd == "/tasks":
            await show_tasks(chat_id, user_id)
        elif cmd == "/shift":
            await show_shift(chat_id, user_id)
        elif cmd == "/team":
            await show_team(chat_id, user_id)
        elif cmd == "/act":
            if await _owner_only(chat_id, user_id):
                await _resend_act(chat_id, user_id)
        else:
            await _send(chat_id, texts.HELP)
        return

    # Вложения: контакт (назначение), фото (закрытие задачи / чек-лист), геолокация
    for a in attachments:
        atype = str(getattr(a, "type", ""))
        if atype.endswith("contact") and state.startswith("assign:"):
            await _assign_from_contact(chat_id, user_id, int(state.split(":")[1]), a)
            return
        if atype.endswith("contact") and state.startswith("claim:"):
            await _claim_with_contact(chat_id, user_id, int(state.split(":")[1]), a)
            return
        if atype.endswith("contact") and state.startswith("join:"):
            await _join_with_contact(chat_id, user_id, int(state.split(":")[1]), a)
            return
        if atype.endswith("contact") and state == "invite":
            await _invite_from_contact(chat_id, user_id, a)
            return
        if atype.endswith("image"):
            url = getattr(getattr(a, "payload", None), "url", None)
            if state.startswith("done:") and url:
                await _close_task_with_photo(chat_id, user_id, int(state.split(":")[1]), url)
                return
            if state.startswith("shift:") and url:
                await _shift_photo_answer(chat_id, user_id, int(state.split(":")[1]), url)
                return
            if url:
                await _attach_shift_photo(chat_id, user_id, url)
                return
        if atype.endswith("location"):
            await _save_geo(chat_id, user_id, getattr(a, "latitude", None), getattr(a, "longitude", None))
            return

    # Текстовые вопросы онбординга
    if state.startswith("onb:"):
        key = state.split(":", 1)[1]
        if key == "confirm":
            await _send(chat_id, "Подтвердите профиль кнопкой выше или заполните заново.", attachments=[kb.confirm_profile_kb()])
            return
        field = get_rulebook().field(key)
        if field.type == "text":
            if not text:
                await _ask(chat_id, key)
                return
            await _advance(chat_id, user_id, key, text[:200])
            return
        await _send(chat_id, "Выберите вариант кнопкой:", attachments=[kb.question_kb(field)])
        return

    if state.startswith("assign:"):
        await _send(chat_id, "Нажмите «Отправить контакт сотрудника» или «Отмена».", attachments=[kb.assign_kb()])
        return
    if state == "invite":
        await _send(chat_id, "Выберите контакт сотрудника или нажмите «Отмена».", attachments=[kb.invite_kb()])
        return
    if state.startswith("claim:") or state.startswith("join:"):
        await _send(chat_id, "Подтвердите номер — отправьте свой контакт кнопкой.", attachments=[kb.claim_kb()])
        return
    if state.startswith("done:"):
        await _send(chat_id, "Пришлите фото результата или закройте без фото.", attachments=[kb.done_kb(int(state.split(':')[1]))])
        return
    if state.startswith("shift:"):
        await _send(chat_id, "Ответьте кнопкой под пунктом или пришлите фото — оно засчитается как «выполнено».")
        return

    await _send(chat_id, "Не понял. " + texts.HELP)


@dp.message_callback()
async def on_callback(cb: MessageCallback):
    payload = (cb.callback.payload or "").strip()
    user_id = cb.callback.user.user_id
    chat_id = cb.message.recipient.chat_id
    parts = payload.split("|")
    head = parts[0]

    with db_session() as db:
        _user_from_event(db, cb.callback.user, chat_id)

    try:
        if head == "ans":
            field = get_rulebook().field(parts[1])
            opt = field.options[int(parts[2])]
            await _replace(cb, f"{field.question}\n— {opt.label}", [], notification=opt.label)
            await _advance(chat_id, user_id, field.key, opt.value)
        elif head == "skip":
            field = get_rulebook().field(parts[1])
            await _replace(cb, f"{field.question}\n— пропущено", [], notification="Пропущено")
            await _advance(chat_id, user_id, field.key, None)
        elif head == "onb" and await _is_staff(user_id):
            await cb.ack(notification="Только владелец")
            await _send(chat_id, texts.OWNER_ONLY)
        elif head == "onb":
            if parts[1] == "confirm":
                await _replace(cb, (cb.message.body.text or "Профиль") + "\n\n✓ Подтверждено", [], notification="Считаю применимые требования…")
                await _confirm_profile(chat_id, user_id)
            else:
                await cb.ack(notification="Заново")
                await start_onboarding(chat_id, user_id, intro=False)
        elif head == "why":
            await cb.ack(notification="Показываю")
            await _why(chat_id, int(parts[1]))
        elif head == "status":
            await cb.ack(notification="Статус")
            await show_status(chat_id, user_id)
        elif head == "tasks":
            await cb.ack(notification="Задачи")
            await show_tasks(chat_id, user_id)
        elif head == "check":
            await cb.ack(notification="Новая самопроверка")
            if await _owner_only(chat_id, user_id):
                await _new_check(chat_id, user_id)
        elif head == "team":
            if parts[1] == "invite":
                if await _owner_only(chat_id, user_id):
                    with db_session() as db:
                        _set_state(db, user_id, "invite", {})
                    await cb.ack(notification="Кого добавить?")
                    await _send(chat_id, "Выберите контакт сотрудника — я подготовлю приглашение.", attachments=[kb.invite_kb()])
                else:
                    await cb.ack(notification="Только владелец")
            else:
                await cb.ack(notification="Команда")
                await show_team(chat_id, user_id)
        elif head == "assign":
            with db_session() as db:
                task = db.get(Task, int(parts[1]))
                is_owner = task is not None and task.owner_id == user_id
            if not is_owner:
                await cb.ack(notification="Только владелец")
                await _send(chat_id, texts.OWNER_ONLY)
                return
            with db_session() as db:
                _set_state(db, user_id, f"assign:{parts[1]}", {})
            await cb.ack(notification="Кого назначить?")
            await _send(chat_id, "Отправьте контакт сотрудника — я запишу его ответственным и дам ссылку, по которой он получит задачу в MAX.", attachments=[kb.assign_kb()])
        elif head == "done":
            with db_session() as db:
                _set_state(db, user_id, f"done:{parts[1]}", {})
            await cb.ack(notification="Пришлите фото")
            await _send(chat_id, "Пришлите фото «как стало» — оно попадёт в акт. Или закройте без фото.", attachments=[kb.done_kb(int(parts[1]))])
        elif head == "done_nophoto":
            await cb.ack(notification="Закрываю")
            await _close_task(chat_id, user_id, int(parts[1]), None)
        elif head == "cancel":
            with db_session() as db:
                _set_state(db, user_id, "idle", {})
            await cb.ack(notification="Отменено")
        elif head == "geo":
            with db_session() as db:
                _set_state(db, user_id, "idle", {})
            await cb.ack(notification="Пропущено")
        elif head == "shift":
            await cb.ack(notification="Чек-лист")
            await show_shift(chat_id, user_id)
        elif head == "shq":
            await _answer_shift_item(cb, int(parts[1]), parts[2], parts[3])
        elif head == "shstop":
            await _shift_summary(chat_id, user_id, int(parts[1]), cb=cb)
        elif head == "shrestart":
            await _restart_shift(cb, int(parts[1]))
        else:
            await cb.ack(notification="Неизвестное действие")
    except Exception as e:  # noqa: BLE001
        log.exception("callback %s: %s", payload, e)
        try:
            await cb.ack(notification="Что-то пошло не так, попробуйте ещё раз")
        except Exception:  # noqa: BLE001
            pass
        await _send(chat_id, "Произошла ошибка, но всё сохранено. Наберите /status, чтобы продолжить.")


# ---------- действия ----------

async def _why(chat_id: int, session_id: int):
    rules = svc.rules_by_id()
    with db_session() as db:
        s = db.get(CheckSession, session_id)
        if s is None:
            await _send(chat_id, "Сессия не найдена. /status")
            return
        groups: dict[str, list[str]] = {}
        for v in s.verdicts:
            if v["applicable"]:
                continue
            reason = "; ".join(v.get("reasons") or []) or "по условиям профиля"
            r = rules.get(v["rule_id"])
            if r:
                groups.setdefault(reason, []).append(f"{r.title} ({r.agency_label})")
    if not groups:
        await _send(chat_id, "Все требования справочника применимы к вашему заведению.")
        return
    await _send(chat_id, texts.why_text(groups))


async def _new_check(chat_id: int, user_id: int):
    with db_session() as db:
        venue = svc.current_venue(db, user_id)
        if venue is None:
            await start_onboarding(chat_id, user_id)
            return
        old = svc.open_session(db, venue)
        if old:
            old.finished_at = utcnow()
        s = svc.start_session(db, venue)
        summ = summary(venue.profile, get_rulebook())
        sid, name = s.id, venue.name
    await _send(chat_id, texts.result_text(name, summ), attachments=[kb.result_kb(sid, summ["not_applicable"])])


async def _resend_act(chat_id: int, user_id: int):
    from ..services.notify import send_act

    with db_session() as db:
        venue = svc.current_venue(db, user_id)
        last = None
        if venue:
            last = db.execute(
                select(CheckSession).where(CheckSession.venue_id == venue.id, CheckSession.finished_at.is_not(None))
                .order_by(CheckSession.finished_at.desc())
            ).scalars().first()
        sid = last.id if last else None
    if sid is None:
        await _send(chat_id, "Завершённых самопроверок ещё нет. Откройте самопроверку через /status.")
        return
    await send_act(sid)


def _norm_phone(p: str | None) -> str:
    return "".join(ch for ch in (p or "") if ch.isdigit())[-10:]


def _invite_text(venue_name: str, title: str, due: str, task_id: int) -> str:
    link = f"https://max.ru/{settings.bot_username}?start=task_{task_id}"
    return (
        f"Вам назначена задача в «{venue_name}»:\n{title}\nСрок: {due}\n\n"
        f"Откройте, чтобы принять и закрыть с фото: {link}"
    )


async def _assign_from_contact(chat_id: int, user_id: int, task_id: int, attachment):
    from urllib.parse import quote

    payload = getattr(attachment, "payload", None)
    name, phone, max_uid, username = "", "", None, None
    try:
        vcf = payload.vcf
        name = vcf.full_name or ""
        phone = vcf.phone or ""
    except Exception:  # noqa: BLE001
        pass
    mi = getattr(payload, "max_info", None)
    if mi is not None:
        max_uid = getattr(mi, "user_id", None)
        username = getattr(mi, "username", None)
        if not name:
            name = f"{getattr(mi, 'first_name', '')} {getattr(mi, 'last_name', '') or ''}".strip()
    with db_session() as db:
        task = db.get(Task, task_id)
        if task is None:
            _set_state(db, user_id, "idle", {})
            await _send(chat_id, "Задача не найдена.")
            return
        if max_uid and max_uid == task.owner_id:
            _set_state(db, user_id, "idle", {})
            await _send(chat_id, "Это ваш собственный контакт. Отправьте контакт сотрудника, которому поручаете задачу.")
            return
        task.assignee_name = name or "Сотрудник"
        task.assignee_phone = phone
        task.assignee_user_id = max_uid  # None, если контакт не связан с аккаунтом MAX — тогда сверим по телефону при открытии
        _set_state(db, user_id, "idle", {})
        title, due = task.title, _fmt_date(task.due_date)
        venue = db.get(Venue, task.venue_id)
        venue_name = venue.name if venue else "заведение"
        if venue is not None:
            svc.invite_staff(db, venue, user_id, task.assignee_name, phone, max_uid)
        assignee_user = db.get(User, max_uid) if max_uid else None

    invite = _invite_text(venue_name, title, due, task_id)
    delivered = False
    if assignee_user and assignee_user.chat_id:
        # Сотрудник уже общался с ботом — задача уходит ему напрямую.
        try:
            await _send(assignee_user.chat_id, invite, attachments=[kb.staff_task_kb(task_id)])
            delivered = True
        except Exception:  # noqa: BLE001
            delivered = False

    who = f"{name or 'сотрудник'}{(' · ' + phone) if phone else ''}"
    if delivered:
        await _send(chat_id, f"Ответственный: {who}. Задача отправлена ему в MAX.")
        return

    # Иначе — одна кнопка открывает «Отправить в MAX» с уже написанным приглашением; остаётся выбрать сотрудника.
    share_url = f"https://max.ru/:share?text={quote(invite, safe='')}"
    profile_url = f"https://max.ru/{username}" if username else None
    await _send(
        chat_id,
        f"Ответственный: {who}.\nНажмите кнопку — откроется отправка в MAX с готовым текстом, выберите сотрудника. "
        "Открыть задачу по ссылке сможет только он.",
        attachments=[kb.assign_result_kb(share_url, profile_url)],
    )


async def _close_task_with_photo(chat_id: int, user_id: int, task_id: int, url: str):
    try:
        rel = await download_from_max(url)
    except Exception as e:  # noqa: BLE001
        log.warning("фото не скачалось: %s", e)
        rel = None
    await _close_task(chat_id, user_id, task_id, rel)


async def _close_task(chat_id: int, user_id: int, task_id: int, photo_rel: str | None):
    with db_session() as db:
        task = db.get(Task, task_id)
        _set_state(db, user_id, "idle", {})
        if task is None or task.status != "open":
            await _send(chat_id, "Задача уже закрыта или не найдена.")
            return
        svc.complete_task(db, task, photo_rel)
        owner = db.get(User, task.owner_id)
        title = task.title
        closer = db.get(User, user_id)
        closer_name = f"{closer.first_name} {closer.last_name}".strip()
    await _send(chat_id, f"Готово: «{title}» закрыта" + (" с фото." if photo_rel else "."))
    if owner and owner.id != user_id and owner.chat_id:
        await _send(owner.chat_id, f"{closer_name} закрыл(а) задачу: {title}" + (" (с фото)" if photo_rel else ""))


async def _attach_shift_photo(chat_id: int, user_id: int, url: str):
    """Фото, присланное вне сценария, прикрепляем к сегодняшнему чек-листу смены как доказательство.

    Каждое фото — своя строка (ShiftPhoto): параллельная присылка нескольких фото не теряет ни одно.
    """
    with db_session() as db:
        venue, _m = svc.role_of(db, user_id)
        if venue is None:
            return
        today = _local_now().strftime("%Y-%m-%d")
        sc = db.execute(select(ShiftCheck).where(ShiftCheck.venue_id == venue.id, ShiftCheck.date == today)).scalars().first()
        if sc is None:
            await _send(chat_id, "Фото получил. Чтобы привязать его к смене, сначала откройте чек-лист: /shift")
            return
        shift_check_id = sc.id
    try:
        rel = await download_from_max(url)
    except Exception as e:  # noqa: BLE001
        log.warning("фото не скачалось: %s", e)
        await _send(chat_id, "Не удалось сохранить фото, попробуйте ещё раз.")
        return
    with db_session() as db:
        db.add(ShiftPhoto(shift_check_id=shift_check_id, photo_path=rel))
        db.flush()
        count = db.execute(
            select(ShiftPhoto).where(ShiftPhoto.shift_check_id == shift_check_id)
        ).scalars().all()
    await _send(chat_id, f"Фото сохранено к смене ({len(count)} шт.).")


async def _save_geo(chat_id: int, user_id: int, lat, lon):
    with db_session() as db:
        venue = svc.current_venue(db, user_id)
        if venue is None or lat is None:
            return
        venue.lat, venue.lon = float(lat), float(lon)
        _set_state(db, user_id, "idle", {})
    await _send(chat_id, "Геолокация заведения сохранена — она попадёт в акт.")
