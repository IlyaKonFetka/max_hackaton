"""Сценарий бота без MAX: фейковый клиент записывает отправленные сообщения, события собираются вручную."""

from types import SimpleNamespace

import pytest

from app.bot import handlers as h
from app.db import ShiftItem, db_session, init_db
from app.services import sessions as svc
from sqlalchemy import select

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

    async def answer(self, notification=None, new_text=None, attachments=None, **kw):
        self.answers.append(notification)
        self.new_text, self.new_attachments = new_text, attachments

    async def ack(self, notification=None):
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
    assert any(getattr(b, "payload", None) == "skip|region" for b in fake.buttons(fake.last()))
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

    # Чек-лист смены — пошагово: заголовок, затем первый пункт полным текстом с короткими кнопками
    await h.on_message(msg("/shift"))
    header, q1 = fake.sent[-2], fake.sent[-1]
    assert "Чек-лист смены" in header["text"] and "пунктов" in header["text"]
    assert q1["text"].startswith("1/")
    q_btns = [b for b in fake.buttons(q1) if getattr(b, "payload", "").startswith("shq|")]
    payloads = {b.payload.split("|")[3] for b in q_btns}
    assert payloads == {"ok", "no", "skip"}
    ok_payload = next(b.payload for b in q_btns if b.payload.endswith("|ok"))
    check_id, rule1 = ok_payload.split("|")[1], ok_payload.split("|")[2]

    # Ответ заменяет вопрос на «пункт — ответ» без кнопок и присылает следующий
    cb = await press(ok_payload)
    assert cb.new_attachments == [] and "выполнено" in cb.new_text
    q2 = fake.last()
    assert q2["text"].startswith("2/")
    rule2 = next(b.payload for b in fake.buttons(q2) if getattr(b, "payload", "").startswith("shq|")).split("|")[2]
    assert rule2 != rule1

    # Двойное нажатие по уже отвеченному пункту — «Уже отмечено», следующий вопрос не дублируется
    sent_before = len(fake.sent)
    cb_dup = await press(ok_payload)
    assert cb_dup.answers == ["Уже отмечено"] and len(fake.sent) == sent_before

    # «Нет» и «Пропустить» — пропущенный уходит в конец
    await press(f"shq|{check_id}|{rule2}|no")
    q3 = fake.last()
    rule3 = next(b.payload for b in fake.buttons(q3) if getattr(b, "payload", "").startswith("shq|")).split("|")[2]
    await press(f"shq|{check_id}|{rule3}|skip")
    assert fake.last()["text"].startswith("3/")  # счётчик не сдвинулся: пропуск — не ответ

    # Пропущенный возвращается в конце; второй пропуск — не зацикливается, а завершает смену
    rest = []
    for _ in range(20):
        btn = next((b for b in fake.buttons(fake.last()) if getattr(b, "payload", "").startswith("shq|")), None)
        if btn is None:
            break
        rid = btn.payload.split("|")[2]
        rest.append(rid)
        await press(f"shq|{check_id}|{rid}|skip")
    assert rest.count(rule3) == 1 and all(rest.count(r) <= 2 for r in rest)  # rule3 уже пропускали до цикла
    assert "Не отмечено" in fake.last()["text"] and "Пройти заново" in [b.text for b in fake.buttons(fake.last())]

    # Заново — начинаем с 1/N
    await press(f"shrestart|{check_id}")
    assert fake.last()["text"].startswith("1/")
    await press(f"shq|{check_id}|{rule1}|ok")

    # «Прервать» заменяет сообщение итогом
    cb_stop = await press(f"shstop|{check_id}")
    assert "выполнено 1 из" in cb_stop.new_text and "Не отмечено" in cb_stop.new_text

    # Повторный /shift продолжает с места остановки
    await h.on_message(msg("/shift"))
    assert "Продолжаем" in fake.sent[-2]["text"]

    # Задач пока нет
    await h.on_message(msg("/tasks"))
    assert "нет" in fake.last()["text"].lower()


@pytest.mark.asyncio
async def test_shift_photo_answers_current_item(fake, monkeypatch):
    """Фото, присланное во время вопроса, засчитывается как «выполнено» именно для этого пункта."""
    uid, chat = 777099, 555099
    with db_session() as db:
        svc.get_or_create_user(db, uid, "Тест2", chat_id=chat)
        svc.save_venue(db, uid, {"activity": "cafe", "has_kitchen": True, "own_production": True,
                                 "seats": 35, "staff": 10, "alcohol": False, "name": "Кафе 2"})

    def msg2(text, attachments=None):
        return SimpleNamespace(message=SimpleNamespace(
            recipient=SimpleNamespace(chat_id=chat, user_id=None),
            sender=SimpleNamespace(user_id=uid, first_name="Тест2", last_name="", username=None),
            body=SimpleNamespace(text=text, attachments=attachments or [], mid="mX"),
        ))

    async def fake_download(url):
        return "photos/fake.jpg"
    monkeypatch.setattr(h, "download_from_max", fake_download)

    await h.on_message(msg2("/shift"))
    q1 = fake.last()
    ok_payload = next(b.payload for b in fake.buttons(q1) if getattr(b, "payload", "").startswith("shq|"))
    check_id, rule1 = int(ok_payload.split("|")[1]), ok_payload.split("|")[2]

    photo = SimpleNamespace(type="image", payload=SimpleNamespace(url="https://example/x.jpg"))
    await h.on_message(msg2("", [photo]))
    assert any("Фото принял" in m["text"] for m in fake.sent[-2:])
    assert fake.last()["text"].startswith("2/")
    with db_session() as db:
        it = db.execute(select(ShiftItem).where(ShiftItem.shift_check_id == check_id, ShiftItem.rule_id == rule1)).scalars().first()
        assert it.status == "ok" and it.photo_path == "photos/fake.jpg"


@pytest.mark.asyncio
async def test_profile_change_resets_today_shift(fake):
    """Сменили профиль — сегодняшний чек-лист смены собирается заново под новый набор требований."""
    uid, chat = 777098, 555098
    with db_session() as db:
        svc.get_or_create_user(db, uid, "Тест3", chat_id=chat)
        svc.save_venue(db, uid, {"activity": "cafe", "has_kitchen": True, "own_production": True,
                                 "seats": 35, "staff": 10, "alcohol": False, "name": "Кафе 3"})

    def msg3(text):
        return SimpleNamespace(message=SimpleNamespace(
            recipient=SimpleNamespace(chat_id=chat, user_id=None),
            sender=SimpleNamespace(user_id=uid, first_name="Тест3", last_name="", username=None),
            body=SimpleNamespace(text=text, attachments=[], mid="mX"),
        ))

    await h.on_message(msg3("/shift"))
    total_cafe = int(fake.last()["text"].split("/")[1].split()[0])
    ok_payload = next(b.payload for b in fake.buttons(fake.last()) if getattr(b, "payload", "").startswith("shq|"))
    cb = FakeCallback(ok_payload)
    cb.callback.user = SimpleNamespace(user_id=uid, first_name="Тест3", last_name="", username=None)
    cb.message.recipient = SimpleNamespace(chat_id=chat)
    await h.on_callback(cb)

    # Профиль стал «кофе навынос, без кухни и работников» — через сервис + тот же путь, что и кнопка подтверждения
    with db_session() as db:
        h._set_state(db, uid, "onb:confirm", {"profile": {"activity": "coffee", "has_kitchen": False, "own_production": False,
                                                          "seats": 0, "staff": 0, "alcohol": False, "name": "Кофе 3"}})
    await h._confirm_profile(chat, uid)

    await h.on_message(msg3("/shift"))
    header = fake.sent[-2]["text"]
    assert "Чек-лист смены" in header and "Продолжаем" not in header  # начали с нуля
    total_coffee = int(fake.last()["text"].split("/")[1].split()[0])
    assert total_coffee < total_cafe


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
    assert "термометр" in fake.last()["text"].lower()
    btns = fake.buttons(fake.last())
    assert any(b.payload == f"assign|{task_id}" for b in btns)

    # Назначение: контакт сотрудника
    await press(f"assign|{task_id}")
    contact = SimpleNamespace(type="contact", payload=SimpleNamespace(
        vcf=SimpleNamespace(full_name="Иван Повар", phone="+79990000000"), max_info=None))
    await h.on_message(msg("", [contact]))
    assert "Иван Повар" in fake.last()["text"]
    share = next(getattr(b, "url", "") for b in fake.buttons(fake.last()) if "max.ru/:share" in getattr(b, "url", ""))
    from urllib.parse import unquote
    assert f"?start=task_{task_id}" in unquote(share) and "Кафе" in unquote(share)

    # Контакт без привязки к аккаунту MAX → бот даёт кнопку «Отправить сотруднику в MAX» (:share с текстом)
    last_btns = fake.buttons(fake.last())
    assert any("max.ru/:share?text=" in getattr(b, "url", "") for b in last_btns)

    # Владелец открыл свою же ссылку — задачу не забирает
    fake.sent.clear()
    await h.on_bot_started(SimpleNamespace(user=user(), chat_id=CHAT, payload=f"task_{task_id}"))
    assert "ссылка для сотрудника" in fake.last()["text"]

    # Посторонний открыл ссылку: аккаунт не назначен, известен телефон → просим подтвердить контакт
    fake.sent.clear()
    stranger = SimpleNamespace(user_id=888, first_name="Иван", last_name="Повар", username=None)
    await h.on_bot_started(SimpleNamespace(user=stranger, chat_id=999, payload=f"task_{task_id}"))
    assert "подтвердить" in fake.last()["text"]
    # Прислал чужой номер — отказ
    wrong = SimpleNamespace(type="contact", payload=SimpleNamespace(
        vcf=SimpleNamespace(full_name="Иван", phone="+79991111111"), max_info=None))
    await h.on_message(SimpleNamespace(message=SimpleNamespace(
        recipient=SimpleNamespace(chat_id=999, user_id=None), sender=stranger,
        body=SimpleNamespace(text="", attachments=[wrong], mid="m"))))
    assert "не совпадает" in fake.last()["text"]
    # Снова по ссылке и правильный номер — принято, владелец уведомлён
    await h.on_bot_started(SimpleNamespace(user=stranger, chat_id=999, payload=f"task_{task_id}"))
    right = SimpleNamespace(type="contact", payload=SimpleNamespace(
        vcf=SimpleNamespace(full_name="Иван Повар", phone="8 (999) 000-00-00"), max_info=None))
    fake.sent.clear()
    await h.on_message(SimpleNamespace(message=SimpleNamespace(
        recipient=SimpleNamespace(chat_id=999, user_id=None), sender=stranger,
        body=SimpleNamespace(text="", attachments=[right], mid="m"))))
    assert any(m["chat_id"] == 999 and "назначена задача" in m["text"] for m in fake.sent)
    assert any(m["chat_id"] == CHAT and "принял" in m["text"] for m in fake.sent)
    # Теперь задача привязана к аккаунту 888 — третий по ссылке получает отказ
    fake.sent.clear()
    other = SimpleNamespace(user_id=777, first_name="Пётр", last_name="", username=None)
    await h.on_bot_started(SimpleNamespace(user=other, chat_id=998, payload=f"task_{task_id}"))
    assert "назначена другому" in fake.last()["text"]

    # Закрытие без фото
    await press(f"done|{task_id}")
    await press(f"done_nophoto|{task_id}")
    assert "закрыта" in fake.last()["text"]
    with db_session() as db:
        assert svc.open_tasks(db, UID) == []


@pytest.mark.asyncio
async def test_team_roles(fake):
    """Владелец приглашает сотрудника через /team; тот присоединяется по ссылке с подтверждением номера,
    видит свой экран, чек-лист смены и только свои задачи; владельческие действия ему закрыты."""
    owner_id, owner_chat = 777050, 555050
    staff_id, staff_chat = 777051, 555051
    owner = SimpleNamespace(user_id=owner_id, first_name="Марина", last_name="", username=None)
    staff = SimpleNamespace(user_id=staff_id, first_name="Иван", last_name="Повар", username=None)

    def m(u, chat, text, attachments=None):
        return SimpleNamespace(message=SimpleNamespace(
            recipient=SimpleNamespace(chat_id=chat, user_id=None), sender=u,
            body=SimpleNamespace(text=text, attachments=attachments or [], mid="m")))

    def cbk(u, chat, payload):
        cb = FakeCallback(payload)
        cb.callback.user = u
        cb.message.recipient = SimpleNamespace(chat_id=chat)
        return cb

    with db_session() as db:
        svc.get_or_create_user(db, owner_id, "Марина", chat_id=owner_chat)
        v = svc.save_venue(db, owner_id, {"activity": "cafe", "has_kitchen": True, "own_production": True,
                                          "seats": 35, "staff": 10, "alcohol": False, "name": "Пекарня"})
        s = svc.start_session(db, v)
        svc.set_answer(db, s, "rpn-temp-log", "violation")
        svc.set_answer(db, s, "rpn-flow", "violation")
        t1, t2 = svc.finish_session(db, s, owner_id)
        task_id = t1.id

    # /team пуст → приглашение контактом → кнопка :share со ссылкой join_
    await h.on_message(m(owner, owner_chat, "/team"))
    assert "Сотрудников пока нет" in fake.last()["text"]
    await h.on_callback(cbk(owner, owner_chat, "team|invite"))
    contact = SimpleNamespace(type="contact", payload=SimpleNamespace(
        vcf=SimpleNamespace(full_name="Иван Повар", phone="+7 999 000-00-00"), max_info=None))
    await h.on_message(m(owner, owner_chat, "", [contact]))
    share = next(getattr(b, "url", "") for b in fake.buttons(fake.last()) if "max.ru/:share" in getattr(b, "url", ""))
    from urllib.parse import unquote
    mid = unquote(share).split("?start=join_")[1].split()[0]

    # Сотрудник открывает ссылку: номер подтверждаем контактом; чужой номер — отказ, свой — в команде
    await h.on_bot_started(SimpleNamespace(user=staff, chat_id=staff_chat, payload=f"join_{mid}"))
    assert "подтвердить" in fake.last()["text"]
    wrong = SimpleNamespace(type="contact", payload=SimpleNamespace(vcf=SimpleNamespace(full_name="Иван", phone="+79991112233"), max_info=None))
    await h.on_message(m(staff, staff_chat, "", [wrong]))
    assert "не совпадает" in fake.last()["text"]
    await h.on_bot_started(SimpleNamespace(user=staff, chat_id=staff_chat, payload=f"join_{mid}"))
    right = SimpleNamespace(type="contact", payload=SimpleNamespace(vcf=SimpleNamespace(full_name="Иван Повар", phone="89990000000"), max_info=None))
    fake.sent.clear()
    await h.on_message(m(staff, staff_chat, "", [right]))
    assert any(x["chat_id"] == staff_chat and "в команде" in x["text"] for x in fake.sent)
    assert any(x["chat_id"] == staff_chat and "Вы здесь сотрудник" in x["text"] for x in fake.sent)
    assert any(x["chat_id"] == owner_chat and "присоединился" in x["text"] for x in fake.sent)

    # /start сотрудника — его экран, не онбординг; /team владельца показывает его «в MAX»
    await h.on_message(m(staff, staff_chat, "/start"))
    assert "Вы здесь сотрудник" in fake.last()["text"]
    await h.on_message(m(owner, owner_chat, "/team"))
    assert "Иван Повар" in fake.last()["text"] and "в MAX" in fake.last()["text"]

    # Владелец назначает задачу этому же контакту — сотрудник уже общался с ботом, задача уходит напрямую
    await h.on_callback(cbk(owner, owner_chat, f"assign|{task_id}"))
    with db_session() as db:
        pass
    contact_linked = SimpleNamespace(type="contact", payload=SimpleNamespace(
        vcf=SimpleNamespace(full_name="Иван Повар", phone="+79990000000"),
        max_info=SimpleNamespace(user_id=staff_id, first_name="Иван", last_name="Повар", username=None)))
    fake.sent.clear()
    await h.on_message(m(owner, owner_chat, "", [contact_linked]))
    direct = next(x for x in fake.sent if x["chat_id"] == staff_chat)
    assert "Вам назначена задача" in direct["text"]
    staff_btns = fake.buttons(direct)
    assert not any("assign|" in getattr(b, "payload", "") for b in staff_btns), "сотруднику нельзя переназначать"
    assert any("фото" in getattr(b, "text", "").lower() for b in staff_btns)

    # /tasks сотрудника — только его задача (одна из двух), без кнопки назначения
    fake.sent.clear()
    await h.on_message(m(staff, staff_chat, "/tasks"))
    task_msgs = [x for x in fake.sent if fake.buttons(x)]
    assert len(task_msgs) == 1 and "Мои задачи — 1" in fake.sent[0]["text"]
    assert not any("assign|" in getattr(b, "payload", "") for b in fake.buttons(task_msgs[0]))

    # Владельческие действия сотруднику закрыты
    await h.on_message(m(staff, staff_chat, "/profile"))
    assert "владельцу" in fake.last()["text"]
    await h.on_message(m(staff, staff_chat, "/act"))
    assert "владельцу" in fake.last()["text"]
    await h.on_callback(cbk(staff, staff_chat, f"assign|{task_id}"))
    assert "владельцу" in fake.last()["text"]

    # А чек-лист смены сотруднику доступен — по заведению владельца
    await h.on_message(m(staff, staff_chat, "/shift"))
    assert "Чек-лист смены" in fake.sent[-2]["text"] and fake.last()["text"].startswith("1/")

    # Сотрудник закрывает задачу с фото → владелец уведомлён
    await h.on_callback(cbk(staff, staff_chat, f"done|{task_id}"))
    assert "Пришлите фото" in fake.last()["text"]
    await h.on_callback(cbk(staff, staff_chat, f"done_nophoto|{task_id}"))
    assert any(x["chat_id"] == owner_chat and "закрыл" in x["text"] for x in fake.sent[-3:])


@pytest.mark.asyncio
async def test_region_from_location(fake, monkeypatch):
    """Вопрос о регионе принимает точку на карте: регион по Nominatim, координаты — в заведение и акт."""
    from app.db import Venue

    uid, chat = 777097, 555097
    u = SimpleNamespace(user_id=uid, first_name="Гео", last_name="", username=None)

    def m(text, attachments=None):
        return SimpleNamespace(message=SimpleNamespace(recipient=SimpleNamespace(chat_id=chat, user_id=None), sender=u,
                                                       body=SimpleNamespace(text=text, attachments=attachments or [], mid="m")))

    async def region_ok(lat, lon):
        return "Республика Татарстан"

    monkeypatch.setattr(h, "region_by_point", region_ok)
    with db_session() as db:
        svc.get_or_create_user(db, uid, "Гео", chat_id=chat)
        h._set_state(db, uid, "onb:region", {"profile": {"activity": "cafe", "has_kitchen": True, "own_production": False,
                                                         "seats": 35, "staff": 3, "alcohol": False}})
    await h._ask(chat, "region")
    assert any("location" in str(getattr(b, "type", "")) for b in fake.buttons(fake.last()))

    point = SimpleNamespace(type="location", latitude=55.7963, longitude=49.1088)
    await h.on_message(m("", [point]))
    assert any("Республика Татарстан" in x["text"] for x in fake.sent[-2:])
    assert "называется" in fake.last()["text"]  # следующий вопрос — название
    await h.on_message(m("Кафе на Кремлёвской"))
    await h._confirm_profile(chat, uid)
    with db_session() as db:
        v = db.query(Venue).filter(Venue.owner_id == uid).one()
        assert v.region == "Республика Татарстан" and round(v.lat, 4) == 55.7963 and "lat" not in v.profile

    # Геокодер недоступен — регион сохраняется координатами
    async def region_none(lat, lon):
        return None

    monkeypatch.setattr(h, "region_by_point", region_none)
    with db_session() as db:
        h._set_state(db, uid, "onb:region", {"profile": {}})
    await h.on_message(m("", [point]))
    assert any("55.7963, 49.1088" in x["text"] for x in fake.sent[-2:])
