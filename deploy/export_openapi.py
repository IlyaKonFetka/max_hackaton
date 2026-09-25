"""Выгрузка OpenAPI-спецификации из кода в openapi.yaml (корень репозитория).

Запуск из backend/:  RUN_BOT=0 python ../deploy/export_openapi.py
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.main import app  # noqa: E402

spec = app.openapi()
spec["info"]["title"] = "Движок применимости — API"
spec["info"]["description"] = (
    "REST-API сервиса «Движок применимости» (бот + мини-приложение MAX). "
    "Публичные методы: /health, /api/rules, /api/applicability. Остальные требуют авторизации: "
    "в мини-приложении — заголовок X-Max-Init-Data (initData MAX, HMAC-SHA256 от токена бота); "
    "для автоматизированной проверки — заголовок X-Api-Key (или Authorization: Bearer) с тестовым ключом."
)
spec["servers"] = [
    {"url": "https://185-246-64-221.sslip.io", "description": "Сервер проверки"},
    {"url": "http://localhost:8000", "description": "Локальный запуск (docker compose up)"},
]
out = Path(__file__).resolve().parents[1] / "openapi.yaml"
out.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
print(f"{out.name}: OpenAPI {spec['openapi']}, путей {len(spec['paths'])}")
