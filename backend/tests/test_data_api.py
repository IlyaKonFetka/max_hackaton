"""Сценарий DATA-API.yaml: то же, что выполнит платформа автоматизированной проверки, по ключу доступа."""

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

K = {"X-Api-Key": "test-key"}
PROFILE = {"activity": "cafe", "has_kitchen": True, "own_production": True, "seats": 35, "staff": 10,
           "alcohol": False, "name": "Тестовое кафе", "region": "Татарстан"}


def test_data_api_scenario():
    init_db()
    with TestClient(app) as c:
        h = c.get("/health").json()
        assert h["status"] == "ok" and h["rules"] > 40

        r = c.get("/api/rules").json()
        assert r["version"] and len(r["rules"]) == h["rules"]

        a = c.post("/api/applicability", json=PROFILE).json()
        assert a["summary"]["applicable"] == len(a["applicable"]) > 20
        assert all(x["reasons"] for x in a["not_applicable"])
        assert c.post("/api/applicability", json={**PROFILE, "activity": "zoo"}).status_code == 422

        # Ключ обязателен и должен быть верным
        assert c.put("/api/venue", json=PROFILE).status_code == 401
        assert c.put("/api/venue", json=PROFILE, headers={"X-Api-Key": "wrong"}).status_code == 401

        v = c.put("/api/venue", json=PROFILE, headers=K).json()
        assert v["name"] == "Тестовое кафе" and v["summary"]["applicable"] == a["summary"]["applicable"]
        # Bearer — альтернатива заголовку
        assert c.put("/api/venue", json=PROFILE, headers={"Authorization": "Bearer test-key"}).status_code == 200

        s = c.post("/api/session/new", headers=K).json()
        sid, rid = s["id"], s["applicable"][0]["id"]
        ans = c.put(f"/api/session/{sid}/answers/{rid}", json={"status": "violation", "comment": "проверка"}, headers=K)
        assert ans.status_code == 200 and ans.json()["progress"]["violation"] == 1

        f = c.post(f"/api/session/{sid}/finish", headers=K).json()
        assert f["counts"]["violation"] == 1 and len(f["tasks"]) == 1
        tid = f["tasks"][0]["id"]

        got = c.get("/api/session", params={"id": sid}, headers=K).json()
        assert got["finished_at"] and got["id"] == sid

        t = c.get("/api/tasks", headers=K).json()
        assert any(x["id"] == tid for x in t["tasks"])
        d = c.post(f"/api/tasks/{tid}/done", headers=K)
        assert d.status_code == 200 and d.json()["status"] == "done"
