"""Движок применимости: `applicable(profile) -> список требований`.

Предикат — дерево из узлов `all` (И), `any` (ИЛИ), `not` и листьев
`{field, eq|ne|in|nin|gt|gte|lt|lte}`. Сложнее не делаем намеренно: редактор справочника
должен прочитать условие вслух и сверить его с текстом НПА.

Для неприменимого пункта движок собирает листья, которые не выполнились, и через подписи полей
профиля превращает их в фразы вроде «Кухня: нет кухни». Отсюда вкладка «Не применимо».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .rules import Field, Rule, Rulebook

_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "nin": lambda a, b: a not in b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
}


@dataclass(frozen=True)
class Verdict:
    rule: Rule
    applicable: bool
    reasons: tuple[str, ...]  # почему НЕ применимо; пусто, если применимо


def _leaf(node: dict, profile: dict, fields: dict[str, Field]) -> tuple[bool, str | None]:
    key = node["field"]
    value = profile.get(key)
    for op, fn in _OPS.items():
        if op in node:
            ok = bool(fn(value, node[op]))
            if ok:
                return True, None
            f = fields.get(key)
            label = f.label if f else key
            actual = f.label_for(value) if f else str(value)
            return False, f"{label}: {actual}"
    raise ValueError(f"лист предиката без оператора: {node}")


def _eval(node: dict, profile: dict, fields: dict[str, Field]) -> tuple[bool, list[str]]:
    """Возвращает (результат, причины-неудачи). Причины собираются только там, где узел провалился."""
    if "field" in node:
        ok, reason = _leaf(node, profile, fields)
        return ok, ([] if ok else [reason])  # type: ignore[list-item]
    if "all" in node:
        reasons: list[str] = []
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
        return (not c_ok), ([] if not c_ok else ["условие исключения выполнено"])
    raise ValueError(f"неизвестный узел предиката: {node}")


def evaluate(rule: Rule, profile: dict, book: Rulebook) -> Verdict:
    if not rule.applies_if:
        return Verdict(rule, True, ())
    fields = {f.key: f for f in book.fields}
    ok, reasons = _eval(rule.applies_if, profile, fields)
    # Убираем дубли, сохраняя порядок.
    uniq = tuple(dict.fromkeys(reasons))
    return Verdict(rule, ok, () if ok else uniq)


def evaluate_all(profile: dict, book: Rulebook) -> list[Verdict]:
    return [evaluate(r, profile, book) for r in book.rules]


def applicable(profile: dict, book: Rulebook) -> list[Rule]:
    return [v.rule for v in evaluate_all(profile, book) if v.applicable]


def summary(profile: dict, book: Rulebook) -> dict[str, Any]:
    """Сводка для сообщения бота: сколько применимо, по ведомствам и периодичности."""
    verdicts = evaluate_all(profile, book)
    app = [v for v in verdicts if v.applicable]
    by_agency: dict[str, int] = {}
    by_period: dict[str, int] = {}
    for v in app:
        by_agency[v.rule.agency_label] = by_agency.get(v.rule.agency_label, 0) + 1
        by_period[v.rule.period] = by_period.get(v.rule.period, 0) + 1
    return {
        "total": len(verdicts),
        "applicable": len(app),
        "not_applicable": len(verdicts) - len(app),
        "by_agency": by_agency,
        "by_period": by_period,
    }


def profile_summary_lines(profile: dict, book: Rulebook) -> list[str]:
    """Читаемая сводка профиля: «Кухня: есть кухня» и т.п."""
    lines = []
    for f in book.fields:
        if f.key in profile and profile[f.key] is not None:
            lines.append(f"{f.label}: {f.label_for(profile[f.key])}")
    return lines
