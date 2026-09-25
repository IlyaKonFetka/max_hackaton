"""Загрузка справочника: профиль объекта и требования ведомств из YAML.

Справочник — данные, а не код. Движок ничего не знает о конкретных ведомствах:
всё, что различает Роспотребнадзор и Роструд, лежит в YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PERIODS = ("once", "shift", "week", "month", "halfyear", "year")
PERIOD_LABELS = {
    "once": "разово",
    "shift": "каждую смену",
    "week": "еженедельно",
    "month": "ежемесячно",
    "halfyear": "раз в полгода",
    "year": "ежегодно",
}
EVIDENCE_LABELS = {"check": "проверка", "photo": "фото", "document": "документ"}
SEVERITY_LABELS = {"high": "высокая", "medium": "средняя", "low": "низкая"}


@dataclass(frozen=True)
class Option:
    value: Any
    label: str


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    question: str
    type: str  # choice | bool | text
    options: tuple[Option, ...] = ()
    optional: bool = False
    skip_label: str | None = None
    location: bool = False  # под вопросом кнопка «Отправить точку на карте»
    value_labels: dict[str, str] = field(default_factory=dict)

    def label_for(self, value: Any) -> str:
        """Человеческая подпись значения — для объяснений «почему не применимо»."""
        if value is None:
            return "не указано"
        key = str(value).lower() if isinstance(value, bool) else str(value)
        if key in self.value_labels:
            return self.value_labels[key]
        for opt in self.options:
            if opt.value == value:
                return opt.label
        return str(value)


@dataclass(frozen=True)
class Source:
    doc: str
    clause: str = ""
    checklist: str = ""
    url: str = ""
    as_of: str = ""
    verified: bool = False


@dataclass(frozen=True)
class Rule:
    id: str
    agency: str
    agency_label: str
    title: str
    check: str
    source: Source
    applies_if: dict | None
    period: str
    evidence: str
    severity: str
    fix_days: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agency": self.agency,
            "agency_label": self.agency_label,
            "title": self.title,
            "check": self.check,
            "source": self.source.__dict__,
            "period": self.period,
            "period_label": PERIOD_LABELS.get(self.period, self.period),
            "evidence": self.evidence,
            "severity": self.severity,
            "fix_days": self.fix_days,
        }


@dataclass(frozen=True)
class Rulebook:
    version: str
    fields: tuple[Field, ...]
    rules: tuple[Rule, ...]

    def field(self, key: str) -> Field:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)

    @property
    def agencies(self) -> list[tuple[str, str]]:
        seen: dict[str, str] = {}
        for r in self.rules:
            seen.setdefault(r.agency, r.agency_label)
        return list(seen.items())


def _parse_fields(raw: dict) -> tuple[Field, ...]:
    out = []
    for f in raw["fields"]:
        out.append(
            Field(
                key=f["key"],
                label=f["label"],
                question=f["question"],
                type=f["type"],
                options=tuple(Option(o["value"], o["label"]) for o in f.get("options", [])),
                optional=bool(f.get("optional", False)),
                skip_label=f.get("skip_label"),
                location=bool(f.get("location", False)),
                value_labels={str(k): v for k, v in (f.get("value_labels") or {}).items()},
            )
        )
    return tuple(out)


def _parse_rules(raw: dict) -> list[Rule]:
    agency = raw["agency"]
    agency_label = raw.get("agency_label", agency)
    rules = []
    for it in raw["items"]:
        period = it.get("period", "once")
        if period not in PERIODS:
            raise ValueError(f"{it['id']}: неизвестная периодичность {period!r}")
        src = it.get("source") or {}
        rules.append(
            Rule(
                id=it["id"],
                agency=agency,
                agency_label=agency_label,
                title=it["title"],
                check=it.get("check", ""),
                source=Source(
                    doc=src.get("doc", ""),
                    clause=src.get("clause", ""),
                    checklist=src.get("checklist", ""),
                    url=src.get("url", ""),
                    as_of=str(src.get("as_of", "")),
                    verified=bool(src.get("verified", False)),
                ),
                applies_if=it.get("applies_if"),
                period=period,
                evidence=it.get("evidence", "check"),
                severity=it.get("severity", "medium"),
                fix_days=int(it.get("fix_days", 7)),
            )
        )
    return rules


def load_rulebook(rules_dir: str | Path) -> Rulebook:
    rules_dir = Path(rules_dir)
    profile_raw = yaml.safe_load((rules_dir / "profile.yaml").read_text(encoding="utf-8"))
    fields = _parse_fields(profile_raw)

    rules: list[Rule] = []
    versions = [str(profile_raw.get("version", ""))]
    batches: list[tuple[int, str, list[Rule]]] = []
    for path in sorted(rules_dir.glob("*.yaml")):
        if path.name == "profile.yaml":
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not raw or "items" not in raw:
            continue
        batches.append((int(raw.get("order", 99)), path.name, _parse_rules(raw)))
        versions.append(str(raw.get("version", "")))
    for _, _, batch in sorted(batches, key=lambda b: (b[0], b[1])):
        rules.extend(batch)

    ids = [r.id for r in rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"Дублирующиеся id требований: {sorted(dupes)}")

    field_keys = {f.key for f in fields}
    for r in rules:
        for key in _predicate_fields(r.applies_if):
            if key not in field_keys:
                raise ValueError(f"{r.id}: предикат ссылается на неизвестное поле {key!r}")

    return Rulebook(version=max(versions), fields=fields, rules=tuple(rules))


def _predicate_fields(node: dict | None) -> set[str]:
    if not node:
        return set()
    if "field" in node:
        return {node["field"]}
    out: set[str] = set()
    for key in ("all", "any"):
        for child in node.get(key, []) or []:
            out |= _predicate_fields(child)
    if "not" in node:
        out |= _predicate_fields(node["not"])
    return out
