"""Помощник на языковой модели: выключен без ключа, отвечает по справочнику заведения, соблюдает лимит."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import db_session, init_db
from app.main import app
from app.services import assistant
from app.services import sessions as svc

UID = 61000001
H = {"X-Debug-User": str(UID)}
CAFE = {"activity": "cafe", "has_kitchen": True, "own_production": True, "seats": 35, "staff": 10, "alcohol": False,
        "services": ["fryer"], "name": "Кафе для помощника"}


@pytest.fixture(scope="module")
def client():
    init_db()
    with db_session() as db:
        svc.get_or_create_user(db, UID, "Тест")
        svc.save_venue(db, UID, CAFE)
    with TestClient(app) as c:
        yield c


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


@pytest.fixture
def llm(monkeypatch):
    """Подключённый помощник с подменённым API: запоминает запрос, отвечает фиксированной фразой."""
    calls = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append({"url": url, "json": json, "headers": headers})
            return FakeResponse("Да, журнал фритюра нужен: п. 44 СанПиН 4282-26.")

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_api_url", "https://llm.example/v1")
    monkeypatch.setattr(settings, "llm_api_key", "secret")
    monkeypatch.setattr(settings, "llm_model", "test-model")
    monkeypatch.setattr(settings, "llm_daily_limit", 2)
    monkeypatch.setattr(assistant.httpx, "AsyncClient", FakeClient)
    assistant._used.clear()
    return calls


def test_disabled_without_key(client, monkeypatch):
    monkeypatch.setattr(settings, "gigachat_auth_key", "")
    assert not assistant.enabled()
    r = client.post("/api/ask", json={"question": "Нужен ли журнал фритюра?"}, headers=H)
    assert r.status_code == 503
    assert client.get("/api/session", headers=H).json()["assistant"] is False


def test_answers_from_venue_rulebook(client, llm):
    r = client.post("/api/ask", json={"question": "Нужен ли журнал фритюра?", "rule_id": "rpn-fryer"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "п. 44" in body["answer"] and body["disclaimer"] and body["remaining"] == 1
    sent = llm[0]
    assert sent["url"] == "https://llm.example/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer secret"
    system = sent["json"]["messages"][0]["content"]
    # В контексте — применимые требования этого кафе с основаниями и неприменимые с причинами
    assert "Фритюрный жир" in system and "п. 44 СанПиН 4282-26" in system
    assert "Не применимы к этому заведению" in system and "Нет доставки и навынос" in system
    # Требования других отраслей перечислены с пометкой, для кого они; периодичность человеческими словами
    assert "Только для: Магазины" in system and "Периодичность: каждую смену" in system
    assert "Вопрос про требование «Фритюрный жир" in sent["json"]["messages"][1]["content"]


def test_daily_limit(client, llm):
    for _ in range(2):
        assert client.post("/api/ask", json={"question": "вопрос"}, headers=H).status_code == 200
    assert client.post("/api/ask", json={"question": "вопрос"}, headers=H).status_code == 429


def test_gigachat_token_is_cached(client, monkeypatch):
    """GigaChat: токен по ключу авторизации берётся один раз и переиспользуется, пока не истёк."""
    import time

    calls = []

    class Resp(FakeResponse):
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, data=None):
            calls.append((url, headers))
            if url.endswith("/oauth"):
                return Resp({"access_token": "tok", "expires_at": int((time.time() + 1800) * 1000)})
            r = Resp({"choices": [{"message": {"content": "Нужен, п. 44."}}]})
            r.status_code = 200
            return r

    monkeypatch.setattr(settings, "llm_provider", "gigachat")
    monkeypatch.setattr(settings, "gigachat_auth_key", "base64key")
    monkeypatch.setattr(settings, "llm_api_url", "")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_daily_limit", 5)
    monkeypatch.setattr(assistant.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(assistant, "_token", {"value": "", "expires": 0.0})
    assistant._used.clear()
    for _ in range(2):
        assert client.post("/api/ask", json={"question": "Нужен ли журнал фритюра?"}, headers=H).status_code == 200
    oauth = [c for c in calls if c[0].endswith("/oauth")]
    chat = [c for c in calls if c[0].endswith("/chat/completions")]
    assert len(oauth) == 1 and oauth[0][1]["Authorization"] == "Basic base64key"
    assert len(chat) == 2 and chat[0][1]["Authorization"] == "Bearer tok"
    assert chat[0][0].startswith("https://gigachat.devices.sberbank.ru/api/v1")
