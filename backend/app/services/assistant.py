"""Помощник: отвечает своими словами на вопрос владельца по его же справочнику.

Решение о применимости принимает движок, а не языковая модель: модель получает готовый список
применимых и неприменимых требований заведения с основаниями и только объясняет его. Если ответа
в списке нет, она должна так и сказать. Так ошибка модели не может «отменить» требование.

Провайдеры:
- gigachat (по умолчанию) — GigaChat API Сбера. По ключу авторизации получаем токен на 30 минут и кешируем.
  Сертификат сервера выдан российским удостоверяющим центром, его корень лежит в app/certs.
  Бесплатный тариф для физлиц допускает один запрос одновременно, поэтому запросы идут по очереди.
- openai — любой API в формате OpenAI Chat Completions (YandexGPT и др.): LLM_API_URL, LLM_API_KEY, LLM_MODEL.
Без ключа помощник выключен, бот работает как раньше.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path

import certifi
import httpx

from ..config import settings
from ..engine import PERIOD_LABELS, evaluate_all, profile_summary_lines
from ..rulebook import get_rulebook

log = logging.getLogger(__name__)

DISCLAIMER = "Ответ составлен ИИ по справочнику сервиса и может быть неточным. Сверяйтесь с основанием в пункте."

GIGACHAT_OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API = "https://gigachat.devices.sberbank.ru/api/v1"
GIGACHAT_MODEL = "GigaChat-2-Max"
RUSSIAN_CA = Path(__file__).resolve().parents[1] / "certs" / "russian_trusted_root_ca.pem"

SYSTEM = (
    "Ты помощник сервиса «Движок применимости» в мессенджере MAX. Владелец кафе, столовой или магазина задаёт вопрос "
    "об обязательных требованиях. Отвечай только по справочнику заведения, который дан ниже: применимые требования, "
    "неприменимые с причинами, основания (пункты НПА и номера вопросов проверочных листов).\n"
    "Правила:\n"
    "1. Не придумывай требования, пункты, сроки, суммы штрафов и ссылки, которых нет в справочнике. Не добавляй "
    "конкретику, которой в справочнике нет: нормы количества, перечни одежды, температуры, сроки перезарядки, "
    "обязательность журналов. Если в справочнике общее требование, так и скажи и сошлись на документ из основания.\n"
    "2. Если ответа в справочнике нет, скажи прямо: «В справочнике сервиса этого нет» и предложи уточнить в "
    "территориальном органе ведомства или у специалиста.\n"
    "3. Называй требование так, как оно названо в справочнике, и основание именно этого требования. "
    "Если у основания в скобках указано, какой пункт о чём, выбирай подходящий к вопросу.\n"
    "3а. «Как часто отмечать в сервисе» — это ритм напоминаний сервиса, а не срок из закона. Не выдавай его за норму.\n"
    "3б. Если в тексте требования есть исключение (например, «при менее чем 25 местах допускается»), и вопрос про него, "
    "прямо ответь «можно» или «нельзя» по этому исключению. Если вопрос противоречит профилю, отвечай по вопросу.\n"
    "4. Отвечай по-русски, коротко: 2–6 предложений, без markdown-разметки и без вступлений.\n"
    "5. Решение, применимо требование или нет, уже принято справочником. Не спорь с ним. Если спрашивают про "
    "требование из списка неприменимых или из другого вида деятельности, скажи, что к заведению оно не относится, и почему."
)

_used: dict[tuple[int, date], int] = defaultdict(int)
_token: dict[str, float | str] = {"value": "", "expires": 0.0}
_lock = asyncio.Lock()


def enabled() -> bool:
    if settings.llm_provider == "gigachat":
        return bool(settings.gigachat_auth_key)
    return bool(settings.llm_api_url and settings.llm_api_key and settings.llm_model)


def remaining(user_id: int) -> int:
    return max(0, settings.llm_daily_limit - _used[(user_id, date.today())])


def build_context(profile: dict) -> str:
    book = get_rulebook()
    verdicts = evaluate_all(profile, book)
    lines = ["Профиль заведения:"]
    lines += [f"- {ln}" for ln in profile_summary_lines(profile, book)]
    lines.append("\nПрименимые требования:")
    for v in verdicts:
        if v.applicable:
            r, s = v.rule, v.rule.source
            lines.append(f"- [{r.agency_label}] {r.title}. Что проверить: {r.check} Основание: {s.doc}, {s.clause}. "
                         f"Проверочный лист: {s.checklist}. Как часто отмечать в сервисе: {PERIOD_LABELS.get(r.period, r.period)}.")
    lines.append("\nНе применимы к этому заведению (с причиной):")
    for v in verdicts:
        if not v.applicable and not v.other_domain:
            lines.append(f"- [{v.rule.agency_label}] {v.rule.title}. Причина: {'; '.join(v.reasons)}. "
                         f"Пояснение: {v.rule.check}")
    other = [v for v in verdicts if v.other_domain]
    if other:
        lines.append("\nТребования для других видов деятельности, к этому заведению не относятся:")
        for v in other:
            lines.append(f"- [{v.rule.agency_label}] {v.rule.title}. Только для: {v.other_domain}; "
                         f"основание: {v.rule.source.clause}.")
    return "\n".join(lines)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    if RUSSIAN_CA.exists():
        ctx.load_verify_locations(str(RUSSIAN_CA))
    return ctx


async def _gigachat_token(client: httpx.AsyncClient) -> str:
    """Токен GigaChat живёт 30 минут; берём новый за минуту до истечения."""
    if _token["value"] and float(_token["expires"]) - 60 > time.time():
        return str(_token["value"])
    r = await client.post(
        GIGACHAT_OAUTH,
        headers={
            "Authorization": f"Basic {settings.gigachat_auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"scope": settings.gigachat_scope},
    )
    r.raise_for_status()
    body = r.json()
    _token["value"] = body["access_token"]
    _token["expires"] = float(body.get("expires_at", 0)) / 1000 or time.time() + 1500
    return str(_token["value"])


async def _complete(messages: list[dict]) -> str:
    payload = {"temperature": 0.2, "max_tokens": 600, "messages": messages}
    if settings.llm_provider == "gigachat":
        async with httpx.AsyncClient(timeout=60, verify=_ssl_context()) as c:
            token = await _gigachat_token(c)
            payload["model"] = settings.llm_model or GIGACHAT_MODEL
            r = await c.post(f"{settings.llm_api_url or GIGACHAT_API}/chat/completions", json=payload,
                             headers={"Authorization": f"Bearer {token}"})
            if r.status_code == 401:  # токен отозвали раньше срока — берём новый один раз
                _token["value"] = ""
                token = await _gigachat_token(c)
                r = await c.post(f"{settings.llm_api_url or GIGACHAT_API}/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {token}"})
    else:
        payload["model"] = settings.llm_model
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(settings.llm_api_url.rstrip("/") + "/chat/completions", json=payload,
                             headers={"Authorization": f"Bearer {settings.llm_api_key}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


async def ask(user_id: int, profile: dict, question: str) -> str | None:
    """Ответ модели или None, если помощник выключен, лимит исчерпан или API не ответил."""
    if not enabled() or remaining(user_id) <= 0:
        return None
    messages = [
        {"role": "system", "content": SYSTEM + "\n\nСправочник заведения:\n" + build_context(profile)},
        {"role": "user", "content": question[:1000]},
    ]
    try:
        async with _lock:  # бесплатный GigaChat обслуживает один запрос за раз
            text = await _complete(messages)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.warning("помощник не ответил: %s", e)
        return None
    _used[(user_id, date.today())] += 1
    return text or None
