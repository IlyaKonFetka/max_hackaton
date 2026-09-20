"""Клавиатуры бота. Payload callback'ов: 'prefix|arg1|arg2' — разбирается в handlers.dispatch_callback."""

from __future__ import annotations

from maxapi.types import (
    CallbackButton,
    LinkButton,
    OpenAppButton,
    RequestContactButton,
    RequestGeoLocationButton,
)
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from ..config import settings
from ..engine import Field


def open_app_button(text: str, payload: str | None = None) -> OpenAppButton:
    from .client import bot

    me = bot.me
    return OpenAppButton(
        text=text,
        web_app=(me.username if me else settings.bot_username),
        contact_id=(me.user_id if me else None),
        payload=payload,
    )


def question_kb(field: Field):
    kb = InlineKeyboardBuilder()
    opts = list(field.options)
    # По две кнопки в ряд для коротких подписей, по одной — для длинных.
    row: list[CallbackButton] = []
    for i, o in enumerate(opts):
        btn = CallbackButton(text=o.label, payload=f"ans|{field.key}|{i}")
        if len(o.label) > 18:
            if row:
                kb.row(*row)
                row = []
            kb.row(btn)
        else:
            row.append(btn)
            if len(row) == 2:
                kb.row(*row)
                row = []
    if row:
        kb.row(*row)
    if field.optional and field.skip_label:
        kb.row(CallbackButton(text=field.skip_label, payload=f"skip|{field.key}"))
    return kb.as_markup()


def text_question_kb(field: Field):
    if not (field.optional and field.skip_label):
        return None
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text=field.skip_label, payload=f"skip|{field.key}"))
    return kb.as_markup()


def confirm_profile_kb():
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="Верно, показать требования", payload="onb|confirm"))
    kb.row(CallbackButton(text="Заполнить заново", payload="onb|restart"))
    return kb.as_markup()


def result_kb(session_id: int, not_applicable: int):
    kb = InlineKeyboardBuilder()
    kb.row(open_app_button("Открыть самопроверку", payload=f"s{session_id}"))
    if not_applicable:
        kb.row(CallbackButton(text=f"Почему не применимо ({not_applicable})", payload=f"why|{session_id}"))
    kb.row(CallbackButton(text="Статус", payload="status"))
    return kb.as_markup()


def status_kb(has_open_session: bool, session_id: int | None, open_tasks: int):
    kb = InlineKeyboardBuilder()
    if has_open_session and session_id:
        kb.row(open_app_button("Продолжить самопроверку", payload=f"s{session_id}"))
    else:
        kb.row(CallbackButton(text="Новая самопроверка", payload="check|new"))
    if open_tasks:
        kb.row(CallbackButton(text=f"Задачи ({open_tasks})", payload="tasks"))
    kb.row(
        CallbackButton(text="Чек-лист смены", payload="shift|show"),
        CallbackButton(text="Изменить профиль", payload="onb|restart"),
    )
    return kb.as_markup()


def task_kb(task_id: int, assigned: bool):
    kb = InlineKeyboardBuilder()
    kb.row(
        CallbackButton(text="Выполнено", payload=f"done|{task_id}"),
        CallbackButton(text=("Сменить ответственного" if assigned else "Назначить ответственного"), payload=f"assign|{task_id}"),
    )
    return kb.as_markup()


def assign_kb():
    kb = InlineKeyboardBuilder()
    kb.row(RequestContactButton(text="Отправить контакт сотрудника"))
    kb.row(CallbackButton(text="Отмена", payload="cancel"))
    return kb.as_markup()


def claim_kb():
    kb = InlineKeyboardBuilder()
    kb.row(RequestContactButton(text="Это я — отправить мой контакт"))
    kb.row(CallbackButton(text="Отмена", payload="cancel"))
    return kb.as_markup()


def done_kb(task_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="Закрыть без фото", payload=f"done_nophoto|{task_id}"))
    kb.row(CallbackButton(text="Отмена", payload="cancel"))
    return kb.as_markup()


def geo_kb():
    kb = InlineKeyboardBuilder()
    kb.row(RequestGeoLocationButton(text="Отправить геолокацию заведения"))
    kb.row(CallbackButton(text="Пропустить", payload="geo|skip"))
    return kb.as_markup()


def shift_item_kb(check_id: int, rule_id: str):
    """Один пункт чек-листа смены за раз: короткие кнопки, полный текст пункта — в теле сообщения."""
    kb = InlineKeyboardBuilder()
    kb.row(
        CallbackButton(text="✅ Выполнено", payload=f"shq|{check_id}|{rule_id}|ok"),
        CallbackButton(text="❌ Нет", payload=f"shq|{check_id}|{rule_id}|no"),
    )
    kb.row(
        CallbackButton(text="Пропустить", payload=f"shq|{check_id}|{rule_id}|skip"),
        CallbackButton(text="Прервать", payload=f"shstop|{check_id}"),
    )
    return kb.as_markup()


def shift_summary_kb(check_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="Пройти заново", payload=f"shrestart|{check_id}"))
    return kb.as_markup()


def assign_result_kb(share_url: str, profile_url: str | None):
    """После выбора контакта: одной кнопкой открыть «Отправить в MAX» с готовым текстом приглашения."""
    kb = InlineKeyboardBuilder()
    kb.row(LinkButton(text="Отправить сотруднику в MAX", url=share_url))
    if profile_url:
        kb.row(LinkButton(text="Профиль сотрудника", url=profile_url))
    return kb.as_markup()


def single_kb(text: str, payload: str):
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text=text, payload=payload))
    return kb.as_markup()
