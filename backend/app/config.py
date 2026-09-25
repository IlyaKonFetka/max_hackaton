"""Настройки из переменных окружения и .env. Токены в код не пишем."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # корень репозитория


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _load_dotenv() -> None:
    """Минимальная поддержка .env в корне репозитория (без зависимости от python-dotenv)."""
    p = BASE_DIR / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


class Settings:
    bot_token: str = _env("MAX_BOT_TOKEN") or ""
    bot_username: str = _env("MAX_BOT_USERNAME", "t419_hakaton_max_bot") or ""
    # Адрес мини-приложения — нужен только для ссылок в тексте; открытие идёт через open_app.
    miniapp_url: str = _env("MINIAPP_URL", "https://ilyakonfetka.github.io/max_hackaton/") or ""
    # Публичный адрес бэкенда — используется в ссылках на фото внутри акта и для CORS-подсказки.
    public_api_url: str = (_env("PUBLIC_API_URL", "http://localhost:8000") or "").rstrip("/")
    cors_origins: list[str] = [
        o.strip()
        for o in (_env("CORS_ORIGINS", "https://ilyakonfetka.github.io,http://localhost:5173") or "").split(",")
        if o.strip()
    ]
    data_dir: Path = Path(_env("DATA_DIR", str(BASE_DIR / "data")) or "")
    rules_dir: Path = Path(_env("RULES_DIR", str(BASE_DIR / "rules")) or "")
    static_dir: Path = Path(_env("STATIC_DIR", str(BASE_DIR / "backend" / "static")) or "")
    host: str = _env("HOST", "0.0.0.0") or "0.0.0.0"
    port: int = int(_env("PORT", "8000") or 8000)
    # Режим разработки: принимать X-Debug-User вместо подписанного initData (никогда не включать в проде).
    debug_auth: bool = (_env("DEBUG_AUTH", "0") or "0") == "1"
    # Ключи доступа к API без MAX (для автоматизированной технической проверки): "ключ:user_id,ключ2:user_id2".
    # Каждый ключ даёт права ровно одного тестового пользователя — подделать реального пользователя MAX нельзя.
    api_test_keys: dict[str, int] = {
        k.strip(): int(v)
        for k, _, v in (p.partition(":") for p in (_env("API_TEST_KEYS", "") or "").split(","))
        if k.strip() and v.strip().isdigit()
    }
    run_bot: bool = (_env("RUN_BOT", "1") or "1") == "1"
    reminder_interval_sec: int = int(_env("REMINDER_INTERVAL_SEC", "60") or 60)
    timezone_offset_hours: int = int(_env("TZ_OFFSET_HOURS", "3") or 3)  # Москва по умолчанию
    # Помощник на языковой модели. Без ключа выключен.
    # gigachat — GigaChat API Сбера (ключ авторизации из личного кабинета, токен обновляется сам);
    # openai — любой API в формате OpenAI Chat Completions (YandexGPT и др.): LLM_API_URL + LLM_API_KEY.
    llm_provider: str = (_env("LLM_PROVIDER", "gigachat") or "gigachat").lower()
    gigachat_auth_key: str = _env("GIGACHAT_AUTH_KEY", "") or ""
    gigachat_scope: str = _env("GIGACHAT_SCOPE", "GIGACHAT_API_PERS") or "GIGACHAT_API_PERS"
    llm_api_url: str = _env("LLM_API_URL", "") or ""
    llm_api_key: str = _env("LLM_API_KEY", "") or ""
    llm_model: str = _env("LLM_MODEL", "") or ""
    # Предохранитель от зависшего цикла, а не ограничение для людей: проверяющий не должен в него упереться.
    llm_daily_limit: int = int(_env("LLM_DAILY_LIMIT", "200") or 200)

    @property
    def db_url(self) -> str:
        return _env("DATABASE_URL") or f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def acts_dir(self) -> Path:
        return self.data_dir / "acts"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.photos_dir.mkdir(parents=True, exist_ok=True)
settings.acts_dir.mkdir(parents=True, exist_ok=True)
