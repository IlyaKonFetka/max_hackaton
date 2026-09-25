"""Помощник: отвечает своими словами на вопрос владельца по его же справочнику.

Решение о применимости принимает движок, а не языковая модель: модель получает готовый список
применимых и неприменимых требований заведения с основаниями и только объясняет его. Если ответа
в списке нет, она должна так и сказать. Так ошибка модели не может «отменить» требование.

Работает с любым API, совместимым с OpenAI Chat Completions (YandexGPT, GigaChat через шлюз, DeepSeek и др.):
LLM_API_URL, LLM_API_KEY, LLM_MODEL. Без ключа помощник выключен, бот работает как раньше.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

import httpx

from ..config import settings
from ..engine import evaluate_all, profile_summary_lines
from ..rulebook import get_rulebook

log = logging.getLogger(__name__)

DISCLAIMER = "Ответ составлен ИИ по справочнику сервиса и может быть неточным. Сверяйтесь с основанием в пункте."

SYSTEM = (
    "Ты помощник сервиса «Движок применимости» в мессенджере MAX. Владелец кафе, столовой или магазина задаёт вопрос "
    "об обязательных требованиях. Отвечай только по справочнику заведения, который дан ниже: применимые требования, "
    "неприменимые с причинами, основания (пункты НПА и номера вопросов проверочных листов).\n"
    "Правила:\n"
    "1. Не придумывай требования, пункты, сроки, суммы штрафов и ссылки, которых нет в справочнике.\n"
    "2. Если ответа в справочнике нет, скажи прямо: «В справочнике сервиса этого нет» и предложи уточнить в "
    "территориальном органе ведомства или у специалиста.\n"
    "3. Называй требование так, как оно названо в справочнике, и его основание.\n"
    "4. Отвечай по-русски, коротко: 2–6 предложений, без markdown-разметки и без вступлений.\n"
    "5. Решение, применимо требование или нет, уже принято справочником. Не спорь с ним."
)

_used: dict[tuple[int, date], int] = defaultdict(int)


def enabled() -> bool:
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
                         f"Проверочный лист: {s.checklist}. Периодичность: {r.period}.")
    lines.append("\nНе применимы к этому заведению (с причиной):")
    for v in verdicts:
        if not v.applicable and not v.other_domain:
            lines.append(f"- [{v.rule.agency_label}] {v.rule.title}. Причина: {'; '.join(v.reasons)}.")
    other = sorted({v.other_domain for v in verdicts if v.other_domain})
    if other:
        lines.append(f"\nВ справочнике есть требования для других видов деятельности ({', '.join(other)}), "
                     "к этому заведению они не относятся.")
    return "\n".join(lines)


async def ask(user_id: int, profile: dict, question: str) -> str | None:
    """Ответ модели или None, если помощник выключен, лимит исчерпан или API не ответил."""
    if not enabled() or remaining(user_id) <= 0:
        return None
    payload = {
        "model": settings.llm_model,
        "temperature": 0.2,
        "max_tokens": 600,
        "messages": [
            {"role": "system", "content": SYSTEM + "\n\nСправочник заведения:\n" + build_context(profile)},
            {"role": "user", "content": question[:1000]},
        ],
    }
    url = settings.llm_api_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(url, json=payload, headers={"Authorization": f"Bearer {settings.llm_api_key}"})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.warning("помощник не ответил: %s", e)
        return None
    _used[(user_id, date.today())] += 1
    return text or None
