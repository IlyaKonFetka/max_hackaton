"""Сценарий бота без MAX: фейковый клиент записывает отправленные сообщения, события собираются вручную."""

from types import SimpleNamespace

import pytest

from app.bot import handlers as h
from app.db import db_session, init_db
from app.services import sessions as svc

UID = 777001
CHAT = 555001


class FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.me = SimpleNamespace(username="t419_hakaton_max_bot", user_id=386056816)

    async def send_message(self, chat_id=None, user_id=None, text=None, attachments=None, **kw):
        self.sent.append({"chat_id": chat_id, "user_id": user_id, "text": text, "attachments": attachments or []})
        return SimpleNamespace(message=SimpleNamespace(body=SimpleNamespace(mid=f"m{len(self.sent)}")))

    async def edit_message(self, message_id, text=None, attachments=None, **kw):
        self.edited.append({"message_id": message_id, "text": text})

    def last(self) -> dict:
        return self.sent[-1]

    def buttons(self, msg: dict) -> list:
        out = []
        for a in msg["attachments"]:
            payload = getattr(a, "payload", None)
            rows = getattr(payload, "buttons", None) or []
            for row in rows:
                out.extend(row)
        return out


@pytest.fixture()
def fake(monkeypatch):
    fb = FakeBot()
    monkeypatch.setattr(h, "bot", fb)
    import app.bot.keyboards as kbmod
    import app.bot.client as client
    monkeypatch.setattr(client, "bot", fb)
    monkeypatch.setattr(kbmod, "settings", kbmod.settings)
    init_db()
    return fb


def user():
    return SimpleNamespace(user_id=UID, first_name="Тест", last_name="Пользователь", username=None)


def msg(text: str, attachments=None):
    return SimpleNamespace(message=SimpleNamespace(
        recipient=SimpleNamespace(chat_id=CHAT, user_id=None),
        sender=user(),
        body=SimpleNamespace(text=text, attachments=attachments or [], mid="mX"),
    ))


class FakeCallback:
    def __init__(self, payload: str, text: str = ""):
        self.callback = SimpleNamespace(payload=payload, user=user(), callback_id="cb1")
        self.message = SimpleNamespace(
            recipient=SimpleNamespace(chat_id=CHAT), body=SimpleNamespace(mid="mQ", text=text, attachments=[])
        )
        self.answers: list = []

    async def answer(self, notification=None, new_text=None, **kw):
        self.answers.append(notification)


async def press(payload: str):
    cb = FakeCallback(payload)
    await h.on_callback(cb)
    return cb


@pytest.mark.asyncio
async def test_onboarding_to_result(fake):
    # /start у нового пользователя → интро + первый вопрос с кнопками
    await h.on_message(msg("/start"))
    assert "Движок применимости" in fake.sent[0]["text"]
    q1 = fake.last()
    btns = fake.buttons(q1)
    assert btns and btns[0].payload.startswith("ans|activity|")

    # Проходим все вопросы кнопками: выбираем кафе, кухня есть, производство есть, 21–50 мест, 6–15 работников, без алкоголя
    choices = {"activity": 0, "has_kitchen": 0, "own_production": 0, "seats": 2, "staff": 2, "alcohol": 1}
    for key, idx in choices.items():
        cur = fake.buttons(fake.last())
        assert any(b.payload.startswith(f"ans|{key}|") for b in cur), f"ожидался вопрос {key}, а пришло: {fake.last()['text']}"
        await press(f"ans|{key}|{idx}")
    # Регион — текст можно пропустить кнопкой; название — текстом
    assert any(b.payload == "skip|region" for b in fake.buttons(fake.last()))
    await press("skip|region")
    assert "называется" in fake.last()["text"]
    await h.on_message(msg("Пекарня на Баумана"))
    assert "Проверьте профиль" in fake.last()["text"]
    assert "Кухня: есть кухня" in fake.last()["text"]

    # Подтверждаем → результат расчёта с кнопкой open_app и «почему не применимо»
    await press("onb|confirm")
    res = fake.last()
    assert "применимо" in res["text"] and "Роспотребнадзор" in res["text"]
    payloads = [getattr(b, "payload", None) for b in fake.buttons(res)]
    assert any(p and p.startswith("why|") for p in payloads)
    types = [str(getattr(b, "type", "")) for b in fake.buttons(res)]
    assert any("open_app" in t for t in types)

    # Объяснение неприменимого
    why = next(p for p in payloads if p and p.startswith("why|"))
    await press(why)
    assert "Не применимо к вашему заведению" in fake.last()["text"]

    # Повторный /start → статус, а не онбординг
    await h.on_message(msg("/start"))
    assert "Пекарня на Баумана" in fake.last()["text"] and "Самопроверка в процессе" in fake.last()["text"]

    # Чек-лист смены: одно сообщение с кнопками, отметка редактирует клавиатуру
    await h.on_message(msg("/shift"))
    sh = fake.last()
    assert "Чек-лист смены" in sh["text"]
    sh_btns = [b for b in fake.buttons(sh) if getattr(b, "payload", "").startswith("sh|")]
    assert len(sh_btns) >= 5
    cb = await press(sh_btns[0].payload)
    assert cb.answers == ["Отмечено"] and fake.edited

    # Задач пока нет
    await h.on_message(msg("/tasks"))
    assert "нет" in fake.last()["text"].lower()


@pytest.mark.asyncio
async def test_tasks_flow(fake):
    # Готовим завершённую самопроверку с нарушением через сервисы
    with db_session() as db:
        svc.get_or_create_user(db, UID, "Тест", chat_id=CHAT)
        v = svc.save_venue(db, UID, {"activity": "cafe", "has_kitchen": True, "own_production": False,
                                     "seats": 35, "staff": 3, "alcohol": False, "name": "Кафе"})
        old = svc.open_session(db, v)
        if old:
            old.finished_at = svc.utcnow()
        s = svc.start_session(db, v)
        svc.set_answer(db, s, "rpn-temp-log", "violation")
        tasks = svc.finish_session(db, s, UID)
        task_id = tasks[0].id

    await h.on_message(msg("/tasks"))
    assert "температуры" in fake.last()["text"]
    btns = fake.buttons(fake.last())
    assert any(b.payload == f"assign|{task_id}" for b in btns)

    # Назначение: контакт сотрудника
    await press(f"assign|{task_id}")
    contact = SimpleNamespace(type="contact", payload=SimpleNamespace(
        vcf=SimpleNamespace(full_name="Иван Повар", phone="+79990000000"), max_info=None))
    await h.on_message(msg("", [contact]))
    assert "Иван Повар" in fake.last()["text"] and "?start=task_" in fake.last()["text"]

    # Сотрудник открывает бота по диплинку
    fake.sent.clear()
    ev = SimpleNamespace(user=SimpleNamespace(user_id=888, first_name="Иван", last_name="Повар", username=None),
                         chat_id=999, payload=f"task_{task_id}")
    await h.on_bot_started(ev)
    assert any(m["chat_id"] == 999 and "назначена задача" in m["text"] for m in fake.sent)
    assert any(m["chat_id"] == CHAT and "принял" in m["text"] for m in fake.sent)

    # Закрытие без фото
    await press(f"done|{task_id}")
    await press(f"done_nophoto|{task_id}")
    assert "закрыта" in fake.last()["text"]
    with db_session() as db:
        assert svc.open_tasks(db, UID) == []
