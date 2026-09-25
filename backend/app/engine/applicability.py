"""Движок применимости: `applicable(profile) -> список требований`.

Предикат — дерево из узлов `all` (И), `any` (ИЛИ), `not` и листьев
`{field, eq|ne|in|nin|gt|gte|lt|lte|has|hasnt}`. Сложнее не делаем намеренно: редактор справочника
должен прочитать условие вслух и сверить его с текстом НПА.

Для неприменимого пункта движок собирает листья, которые не выполнились, и через подписи полей
профиля превращает их в фразы вроде «Кухня: нет кухни». Отсюда вкладка «Не применимо».
Если пункт отсечён по виду заведения, он относится к другой отрасли: такие показываем свёрнуто,
иначе кафе увидит в «не применимо» требования для магазинов и решит, что документы на продукты ему не нужны.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .rules import Checklist, Field, Rule, Rulebook

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "nin": lambda a, b: a not in b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "has": lambda a, b: isinstance(a, (list, tuple, set)) and b in a,
    "hasnt": lambda a, b: not (isinstance(a, (list, tuple, set)) and b in a),
}

ACTIVITY_FIELD = "activity"


@dataclass(frozen=True)
class Verdict:
    rule: Rule
    applicable: bool
    reasons: tuple[str, ...]  # почему НЕ применимо; пусто, если применимо
    other_domain: str = ""  # не применимо, потому что требование для другого вида деятельности


def _leaf(node: dict, profile: dict, fields: dict[str, Field]) -> tuple[bool, str | None, str]:
    key = node["field"]
    value = profile.get(key)
    for op, fn in _OPS.items():
        if op in node:
            ok = bool(fn(value, node[op]))
            if ok:
                return True, None, key
            f = fields.get(key)
            if op in ("has", "hasnt") and f:
                opt = f.option(node[op])
                if op == "has":
                    text = (opt.absent if opt and opt.absent else f"нет: {opt.label if opt else node[op]}")
                else:
                    text = f"есть: {opt.label if opt else node[op]}"
                return False, text[:1].upper() + text[1:], key
            label = f.label if f else key
            actual = f.label_for(value) if f else str(value)
            return False, f"{label}: {actual}", key
    raise ValueError(f"лист предиката без оператора: {node}")


def _eval(node: dict, profile: dict, fields: dict[str, Field]) -> tuple[bool, list[tuple[str, str]]]:
    """Возвращает (результат, [(причина, поле)]). Причины собираются только там, где узел провалился."""
    if "field" in node:
        ok, reason, key = _leaf(node, profile, fields)
        return ok, ([] if ok else [(reason, key)])  # type: ignore[list-item]
    if "all" in node:
        reasons: list[tuple[str, str]] = []
        ok = True
        for child in node["all"]:
            c_ok, c_reasons = _eval(child, profile, fields)
            if not c_ok:
                ok = False
                reasons.extend(c_reasons)
        return ok, reasons
    if "any" in node:
        reasons = []
        for child in node["any"]:
            c_ok, c_reasons = _eval(child, profile, fields)
            if c_ok:
                return True, []
            reasons.extend(c_reasons)
        return False, reasons
    if "not" in node:
        c_ok, _ = _eval(node["not"], profile, fields)
        return (not c_ok), ([] if not c_ok else [("условие исключения выполнено", "")])
    raise ValueError(f"неизвестный узел предиката: {node}")


def check(pred: dict | None, profile: dict, book: Rulebook) -> tuple[bool, list[tuple[str, str]]]:
    if not pred:
        return True, []
    fields = {f.key: f for f in book.fields}
    return _eval(pred, profile, fields)


def normalize_profile(profile: dict, book: Rulebook) -> dict:
    """Подставляет значения по умолчанию для полей, которые этому заведению не задают (ask_if)."""
    out = dict(profile)
    for f in book.fields:
        if f.key in out and out[f.key] is not None:
            continue
        if f.default is not None:
            out[f.key] = list(f.default) if isinstance(f.default, list) else f.default
    return out


def should_ask(f: Field, profile: dict, book: Rulebook) -> bool:
    ok, _ = check(f.ask_if, profile, book)
    return ok


def evaluate(rule: Rule, profile: dict, book: Rulebook) -> Verdict:
    if not rule.applies_if:
        return Verdict(rule, True, ())
    ok, reasons = check(rule.applies_if, profile, book)
    if ok:
        return Verdict(rule, True, ())
    uniq = tuple(dict.fromkeys(r for r, _ in reasons))
    other = any(key == ACTIVITY_FIELD for _, key in reasons)
    return Verdict(rule, False, uniq, (rule.domain or "Другой вид деятельности") if other else "")


def evaluate_all(profile: dict, book: Rulebook) -> list[Verdict]:
    profile = normalize_profile(profile, book)
    return [evaluate(r, profile, book) for r in book.rules]


def applicable(profile: dict, book: Rulebook) -> list[Rule]:
    return [v.rule for v in evaluate_all(profile, book) if v.applicable]


def summary(profile: dict, book: Rulebook) -> dict[str, Any]:
    """Сводка для сообщения бота: сколько применимо, по ведомствам и периодичности."""
    verdicts = evaluate_all(profile, book)
    app = [v for v in verdicts if v.applicable]
    other = [v for v in verdicts if not v.applicable and v.other_domain]
    by_agency: dict[str, int] = {}
    by_period: dict[str, int] = {}
    for v in app:
        by_agency[v.rule.agency_label] = by_agency.get(v.rule.agency_label, 0) + 1
        by_period[v.rule.period] = by_period.get(v.rule.period, 0) + 1
    return {
        "total": len(verdicts),
        "applicable": len(app),
        "not_applicable": len(verdicts) - len(app) - len(other),
        "other_domain": len(other),
        "by_agency": by_agency,
        "by_period": by_period,
        "funnel": funnel(profile, book, verdicts),
    }


AGENCY_LABELS = {"rospotrebnadzor": "Роспотребнадзор", "rostrud": "Роструд", "mchs": "МЧС России"}


def checklist_status(cl: Checklist, profile: dict, book: Rulebook) -> dict[str, Any]:
    """Относится ли проверочный лист к заведению: yes | maybe | no | other."""
    base = {"id": cl.id, "agency": cl.agency, "number": cl.number, "title": cl.title, "doc": cl.doc}
    if cl.industry:
        return {**base, "status": "other", "reason": cl.industry}
    ok, reasons = check(cl.applies_if, profile, book)
    if not ok:
        other = any(key == ACTIVITY_FIELD for _, key in reasons)
        text = "; ".join(dict.fromkeys(r for r, _ in reasons))
        return {**base, "status": "other" if other else "no", "reason": text}
    if cl.when and (cl.when_if is None or check(cl.when_if, profile, book)[0]):
        return {**base, "status": "maybe", "reason": cl.when}
    return {**base, "status": "yes", "reason": ""}


def funnel(profile: dict, book: Rulebook, verdicts: list[Verdict] | None = None) -> dict[str, Any]:
    """Воронка «все проверочные листы → ваши листы → вопросы вашего листа → пункты самопроверки»."""
    profile = normalize_profile(profile, book)
    verdicts = verdicts if verdicts is not None else evaluate_all(profile, book)
    statuses = [checklist_status(c, profile, book) for c in book.checklists]

    agencies = []
    for code, label in AGENCY_LABELS.items():
        own = [s for s in statuses if s["agency"] == code]
        if not own:
            continue
        agencies.append({
            "agency": code,
            "label": label,
            "total": len(own),
            "yes": sum(s["status"] == "yes" for s in own),
            "maybe": sum(s["status"] == "maybe" for s in own),
            "other": sum(s["status"] in ("other", "no") for s in own),
        })

    # Лист, разобранный полностью: считаем по вопросам, откуда что взялось
    detailed = []
    for cl in book.checklists:
        if cl.coverage != "full" or not cl.questions:
            continue
        st = next(s for s in statuses if s["id"] == cl.id)
        if st["status"] != "yes":
            continue
        app_q: set[int] = set()
        for v in verdicts:
            if v.applicable:
                for cid, qs in v.rule.refs:
                    if cid == cl.id:
                        app_q |= set(qs)
        other_q: set[int] = set()
        for sc in cl.other_scopes:
            other_q |= set(sc.questions)
        other_q -= app_q
        total = cl.questions
        detailed.append({
            "id": cl.id,
            "title": cl.title,
            "doc": cl.doc,
            "questions": total,
            "applicable": len(app_q),
            "other_domain": len(other_q),
            "not_applicable": total - len(app_q) - len(other_q),
            "other_scopes": [sc.label for sc in cl.other_scopes],
        })

    return {
        "checklists_total": len(statuses),
        "checklists_yes": sum(s["status"] == "yes" for s in statuses),
        "checklists_maybe": sum(s["status"] == "maybe" for s in statuses),
        "agencies": agencies,
        "detailed": detailed,
        "rules_applicable": sum(v.applicable for v in verdicts),
    }


def checklists_for(profile: dict, book: Rulebook) -> list[dict[str, Any]]:
    profile = normalize_profile(profile, book)
    return [checklist_status(c, profile, book) for c in book.checklists]


def profile_summary_lines(profile: dict, book: Rulebook) -> list[str]:
    """Читаемая сводка профиля: «Кухня: есть кухня» и т.п."""
    lines = []
    for f in book.fields:
        if f.key in profile and profile[f.key] is not None:
            if f.ask_if and not should_ask(f, profile, book):
                continue
            lines.append(f"{f.label}: {f.label_for(profile[f.key])}")
    return lines
