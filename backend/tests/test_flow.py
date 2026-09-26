import io

import pytest
from fastapi.testclient import TestClient

from app.db import db_session, init_db
from app.main import app
from app.services import sessions as svc

UID = 54750958
H = {"X-Debug-User": str(UID)}


@pytest.fixture(scope="module")
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def _png_bytes() -> bytes:
    # Минимальный валидный PNG 1x1.
    import base64
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def test_unauthorized(client):
    assert client.get("/api/me").status_code == 401


def test_me_without_venue(client):
    r = client.get("/api/me", headers=H)
    assert r.status_code == 200 and r.json()["venue"] is None
    assert client.get("/api/session", headers=H).status_code == 404


def test_full_flow(client):
    # Онбординг (то, что делает бот) — через сервисный слой.
    with db_session() as db:
        svc.get_or_create_user(db, UID, "Илья", chat_id=339848350)
        svc.save_venue(db, UID, {"activity": "cafe", "has_kitchen": True, "own_production": True,
                                 "seats": 35, "staff": 10, "alcohol": False, "region": "Татарстан", "name": "Пекарня"})

    s = client.get("/api/session", headers=H).json()
    assert s["venue"]["name"] == "Пекарня"
    assert s["progress"]["total"] == len(s["applicable"]) > 15
    assert s["not_applicable"] and all(x["reasons"] for x in s["not_applicable"])
    ids = [a["id"] for a in s["applicable"]]
    assert "rpn-flow" in ids and "rt-contracts" in ids
    sid = s["id"]

    # Ответы: одно нарушение с фото и комментарием, остальное — соблюдается.
    r = client.put(f"/api/session/{sid}/answers/rpn-flow", json={"status": "violation", "comment": "журнал не вёлся 3 дня"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "violation"
    r = client.post(f"/api/session/{sid}/answers/rpn-flow/photo",
                    files={"file": ("j.png", io.BytesIO(_png_bytes()), "image/png")}, headers=H)
    assert r.status_code == 200 and r.json()["photo_url"].endswith(".jpg")  # нормализуется в JPEG
    for rid in ids:
        if rid != "rpn-flow":
            assert client.put(f"/api/session/{sid}/answers/{rid}", json={"status": "ok"}, headers=H).status_code == 200
    # Чужое требование отклоняется.
    assert client.put(f"/api/session/{sid}/answers/nope", json={"status": "ok"}, headers=H).status_code == 400

    # Завершение → задачи.
    r = client.post(f"/api/session/{sid}/finish", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["violation"] == 1 and body["counts"]["ok"] == len(ids) - 1
    assert len(body["tasks"]) == 1 and body["tasks"][0]["rule_id"] == "rpn-flow"

    # Повторный ответ в закрытую сессию запрещён; статус возвращает завершённую.
    assert client.put(f"/api/session/{sid}/answers/rpn-temp-log", json={"status": "ok"}, headers=H).status_code == 409
    me = client.get("/api/me", headers=H).json()
    assert me["session"]["finished"] is True and me["open_tasks"] == 1

    # Задачи и закрытие с фото.
    t = client.get("/api/tasks", headers=H).json()["tasks"]
    assert len(t) == 1
    r = client.post(f"/api/tasks/{t[0]['id']}/done", files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["photo_after_url"]
    assert client.get("/api/me", headers=H).json()["open_tasks"] == 0

    # PDF-акт собирается и содержит страницы.
    from app.db import CheckSession, Venue
    from app.services.pdf import build_act
    with db_session() as db:
        sess = db.get(CheckSession, sid)
        venue = db.get(Venue, sess.venue_id)
        path = build_act(sess, venue, "Илья")
    assert path.exists() and path.stat().st_size > 10_000
    from pypdf import PdfReader
    text = "".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
    assert "Акт самопроверки" in text and "Пекарня" in text and "Проверочные листы" in text
    assert "для других видов деятельности" in text  # магазины и столовые соцучреждений — одной строкой

    # Новая сессия — старая закрыта, счётчики обнулены.
    s2 = client.post("/api/session/new", headers=H).json()
    assert s2["id"] != sid and s2["progress"]["answered"] == 0


def test_second_profile_is_smaller(client):
    with db_session() as db:
        svc.save_venue(db, UID, {"activity": "coffee", "has_kitchen": False, "own_production": False,
                                 "seats": 0, "staff": 0, "alcohol": False, "name": "Кофе с собой"})
    s = client.post("/api/session/new", headers=H).json()
    assert len(s["applicable"]) <= 20
    assert any("нет кухни" in r for x in s["not_applicable"] for r in x["reasons"])


def test_upload_applies_exif_orientation(client):
    """Фото с EXIF Orientation=6 (повёрнуто на 90°) после сохранения физически повёрнуто: reportlab EXIF не читает."""
    import io as _io
    from PIL import Image
    from app.services.media import save_upload, abs_path

    img = Image.new("RGB", (40, 20), "red")  # альбомная 40x20
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: Rotate 90 CW
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    rel = save_upload(buf.getvalue(), "image/jpeg", "p.jpg")
    saved = Image.open(abs_path(rel))
    assert saved.size == (20, 40), "ориентация должна быть применена к пикселям"
    assert not saved.getexif().get(0x0112), "EXIF-ориентация должна быть сброшена"


def test_health_reports_stopped_bot(client, monkeypatch):
    """Если задача бота завершилась, /health отвечает 503: Docker перезапустит контейнер, мониторинг пришлёт уведомление."""
    import asyncio

    import app.main as main

    assert client.get("/health").json()["bot"] == "disabled"  # в тестах бот не запускается
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    fut.set_result(None)
    monkeypatch.setattr(main, "_bot_tasks", {"bot-polling": fut})
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["bot"] == "stopped" and r.json()["ok"] is False
    loop.close()
