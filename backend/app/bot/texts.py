"""Длинные тексты бота. Короткие реплики живут рядом с обработчиками."""

from __future__ import annotations

from ..engine import PERIOD_LABELS

APP_NAME = "Движок применимости"

INTRO = (
    f"{APP_NAME}. Самопроверка для кафе, столовых, пекарен и магазинов у дома.\n\n"
    "Ответьте на восемь вопросов о заведении, почти все кнопками. По ответам я отберу требования "
    "Роспотребнадзора, Роструда и МЧС, которые к вам относятся, и объясню, почему остальные не относятся.\n\n"
    "Потом можно пройти самопроверку, получить акт в PDF и план устранения нарушений. О сроках напомню.\n\n"
    "Первый вопрос:"
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
        lines.append(f"Не применимо: {summ['not_applicable']}. Причины покажу по кнопке ниже.")
    lines.append("")
    lines.append("Дальше откройте самопроверку и отметьте каждый пункт: соблюдается, нарушение или не знаю.")
    return "\n".join(lines)


def why_text(groups: dict[str, list[str]]) -> str:
    lines = ["Не применимо к вашему заведению:", ""]
    for reason, titles in groups.items():
        lines.append(f"▸ {reason}")
        for t in titles:
            lines.append(f"   – {t}")
        lines.append("")
    lines.append("Если что-то поменяется, например появится кухня или вы наймёте людей, пройдите /profile заново, и список пересчитается.")
    return "\n".join(lines).strip()
