"""Длинные тексты бота. Короткие реплики живут рядом с обработчиками."""

from __future__ import annotations


APP_NAME = "Движок применимости"

INTRO = (
    f"{APP_NAME}. Самопроверка для кафе, столовых, пекарен и магазинов у дома.\n\n"
    "Ответьте на несколько вопросов о заведении, почти все кнопками. По ответам я отберу из проверочных листов "
    "Роспотребнадзора, Роструда и МЧС то, что относится к вам, и объясню, почему остальное не относится.\n\n"
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
    "/help — эта справка\n\n"
    "На сервере подключён помощник. Можно просто написать вопрос своими словами, "
    "например «нужен ли мне журнал бракеража?». Он ответит по справочнику вашего заведения."
)

OWNER_ONLY = "Это действие доступно владельцу заведения."


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def funnel_lines(f: dict) -> list[str]:
    """Воронка: все листы ведомств → ваши листы → вопросы полностью разобранного листа."""
    lines = []
    if f.get("checklists_total"):
        yes, maybe = f["checklists_yes"], f["checklists_maybe"]
        line = f"Из {f['checklists_total']} проверочных листов к вам {plural(yes, 'относится', 'относятся', 'относятся')} {yes}"
        line += f" (ещё {maybe} — при условиях)." if maybe else "."
        lines.append(line)
    for d in f.get("detailed", []):
        lines.append(f"Лист Роспотребнадзора для общепита: ваши {d['applicable']} вопросов из {d['questions']}.")
    return lines


def result_text(venue_name: str, summ: dict) -> str:
    lines = [venue_name, ""]
    fl = funnel_lines(summ.get("funnel") or {})
    if fl:
        lines.extend(fl)
        lines.append("")
    n = summ["applicable"]
    lines.append(f"В самопроверке {n} {plural(n, 'пункт', 'пункта', 'пунктов')}:")
    for agency, k in summ["by_agency"].items():
        lines.append(f"• {agency} — {k}")
    if summ["not_applicable"]:
        lines.append("")
        lines.append(f"Не относится к вам: {summ['not_applicable']}, причины — по кнопке ниже.")
    lines.append("")
    lines.append("Откройте самопроверку и отметьте пункты.")
    return "\n".join(lines)


def why_text(groups: dict[str, list[str]], other: dict[str, int] | None = None) -> str:
    lines = ["Не применимо к вашему заведению:", ""] if groups else []
    for reason, titles in groups.items():
        lines.append(f"▸ {reason}")
        for t in titles:
            lines.append(f"   – {t}")
        lines.append("")
    if other:
        total = sum(other.values())
        parts = ", ".join(f"{d.lower()} — {n}" for d, n in other.items())
        lines.append(f"Ещё {total} {plural(total, 'требование', 'требования', 'требований')} справочника для других видов "
                     f"деятельности ({parts}). Их не показываю.")
        lines.append("")
    lines.append("Если что-то поменяется, например появится кухня, доставка или вы наймёте людей, пройдите /profile заново, "
                 "и список пересчитается.")
    return "\n".join(lines).strip()
