"""Тексты бота в одном месте."""

from __future__ import annotations

from ..engine import PERIOD_LABELS

APP_NAME = "Движок применимости"

INTRO = (
    f"{APP_NAME} — самопроверка для кафе и небольших заведений общепита.\n\n"
    "За 7 коротких вопросов я определю, какие обязательные требования Роспотребнадзора, "
    "Роструда и МЧС относятся именно к вашему заведению, а какие — нет и почему. "
    "Дальше — самопроверка по ним, акт в PDF и план устранения с напоминаниями.\n\n"
    "Поехали. Первый вопрос:"
)

HELP = (
    "Команды:\n"
    "/start — начать или показать статус\n"
    "/status — состояние заведения: самопроверка, задачи, сроки\n"
    "/tasks — открытые задачи по нарушениям\n"
    "/shift — чек-лист смены\n"
    "/team — сотрудники заведения (владелец)\n"
    "/act — последний акт самопроверки (владелец)\n"
    "/profile — заполнить профиль заново (владелец)\n"
    "/help — эта справка"
)

OWNER_ONLY = "Это действие доступно владельцу заведения."

def result_text(venue_name: str, summ: dict) -> str:
    lines = [f"Заведение: {venue_name}", ""]
    lines.append(f"По вашему профилю применимо {summ['applicable']} требований из {summ['total']}:")
    for agency, n in summ["by_agency"].items():
        lines.append(f"• {agency} — {n}")
    if summ["by_period"]:
        parts = [f"{PERIOD_LABELS[p]} — {n}" for p, n in sorted(summ["by_period"].items(), key=lambda x: x[0])]
        lines.append("")
        lines.append("Периодичность: " + ", ".join(parts))
    if summ["not_applicable"]:
        lines.append("")
        lines.append(f"Не применимо: {summ['not_applicable']} — нажмите, чтобы увидеть почему.")
    lines.append("")
    lines.append("Откройте самопроверку и пройдите по списку: по каждому пункту — соблюдается, нарушение или не знаю.")
    return "\n".join(lines)


def why_text(groups: dict[str, list[str]]) -> str:
    lines = ["Не применимо к вашему заведению:", ""]
    for reason, titles in groups.items():
        lines.append(f"▸ {reason}")
        for t in titles:
            lines.append(f"   – {t}")
        lines.append("")
    lines.append("Если профиль изменится (появится кухня, наймёте работников), список пересчитается автоматически.")
    return "\n".join(lines).strip()
