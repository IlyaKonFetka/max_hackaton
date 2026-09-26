"""Загрузка справочника: профиль объекта и требования ведомств из YAML.

Справочник — данные, а не код. Движок ничего не знает о конкретных ведомствах:
всё, что различает Роспотребнадзор и Роструд, лежит в YAML.
"""

from __future__ import annotations

import re
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
    absent: str = ""  # для множественного выбора: как сказать, что варианта нет («нет фритюра»)


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    question: str
    type: str  # choice | bool | text | multi
    options: tuple[Option, ...] = ()
    optional: bool = False
    skip_label: str | None = None
    location: bool = False  # под вопросом кнопка «Отправить точку на карте»
    value_labels: dict[str, str] = field(default_factory=dict)
    ask_if: dict | None = None  # вопрос задаётся, только если условие выполнено; иначе в профиль идёт default
    default: Any = None
    done_label: str = "Готово"

    def option(self, value: Any) -> Option | None:
        return next((o for o in self.options if o.value == value), None)

    def label_for(self, value: Any) -> str:
        """Человеческая подпись значения — для объяснений «почему не применимо»."""
        if value is None:
            return "не указано"
        if isinstance(value, (list, tuple)):
            labels = [o.label for o in self.options if o.value in value]
            return ", ".join(labels) if labels else "ничего из списка"
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
    section: str = ""
    domain: str = ""  # вид деятельности, для которого требование; нужен, когда условие отсекает по виду заведения
    refs: tuple[tuple[str, tuple[int, ...]], ...] = ()  # (id проверочного листа, номера вопросов)
    # Объяснение для тех, к кому пункт не относится, если это важно показать (например, изменение СанПиН);
    # показывается вместо «другого вида деятельности», когда выполнено explain_if
    explain: str = ""
    explain_if: dict | None = None

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
            "section": self.section,
            "refs": [{"checklist": c, "questions": list(q)} for c, q in self.refs],
        }


@dataclass(frozen=True)
class OtherScope:
    label: str
    clause: str
    questions: tuple[int, ...]


@dataclass(frozen=True)
class Checklist:
    """Проверочный лист ведомства целиком: чтобы показать, какие листы относятся к заведению."""

    id: str
    agency: str
    number: str
    title: str
    doc: str
    questions: int | None = None
    coverage: str = ""  # full | partial | ""
    applies_if: dict | None = None
    when: str = ""  # условие вне профиля: лист относится, «если …»
    when_if: dict | None = None  # если задан, when действует только при выполнении этого условия
    industry: str = ""  # лист другой отрасли
    other_scopes: tuple[OtherScope, ...] = ()


@dataclass(frozen=True)
class Rulebook:
    version: str
    fields: tuple[Field, ...]
    rules: tuple[Rule, ...]
    checklists: tuple[Checklist, ...] = ()

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
                options=tuple(Option(o["value"], o["label"], o.get("absent", "")) for o in f.get("options", [])),
                optional=bool(f.get("optional", False)),
                skip_label=f.get("skip_label"),
                location=bool(f.get("location", False)),
                value_labels={str(k): v for k, v in (f.get("value_labels") or {}).items()},
                ask_if=f.get("ask_if"),
                default=f.get("default"),
                done_label=f.get("done_label", "Готово"),
            )
        )
    return tuple(out)


_QUESTIONS = re.compile(r"вопрос(?:ы)?\s+(\d[\d\s,–-]*)")


def _numbers(text: str) -> tuple[int, ...]:
    """«2–13, 21, 59» → (2, …, 13, 21, 59)."""
    out: list[int] = []
    for part in re.split(r"\s*,\s*", text.strip().rstrip(",")):
        m = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if m:
            out.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return tuple(out)


def parse_refs(checklist: str) -> tuple[tuple[str, tuple[int, ...]], ...]:
    """Ссылки на проверочные листы из строки источника.

    Номера записаны в одном месте, в человеческой строке, чтобы текст в акте и данные воронки не разошлись.
    Форматы: «Приказ № 808, прил. № 2, вопросы 2–13»; «Приказ Роструда № 20, приложения 6 «…» и 7 «…»»;
    «Приказ МЧС России … № 78, форма 1, вопросы 152–154».
    """
    q = _QUESTIONS.search(checklist)
    questions = _numbers(q.group(1)) if q else ()
    if "808" in checklist:
        m = re.search(r"прил\.\s*№\s*(\d)", checklist)
        if m and questions:
            return ((f"rpn-808-{m.group(1)}", questions),)
    elif "Роструда" in checklist:
        forms = re.findall(r"(?:приложени[ея]|и)\s+(\d+)\s+«", checklist)
        return tuple((f"rt-{n}", ()) for n in forms)
    elif "МЧС" in checklist:
        m = re.search(r"форма\s+(\d+)", checklist)
        if m:
            return ((f"mchs-{m.group(1)}", questions),)
    return ()


def _parse_rules(raw: dict) -> list[Rule]:
    agency = raw["agency"]
    agency_label = raw.get("agency_label", agency)
    file_section = raw.get("section", "")
    file_domain = raw.get("domain", "")
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
                section=it.get("section", file_section),
                domain=it.get("domain", file_domain),
                refs=parse_refs(src.get("checklist", "")),
                explain=it.get("explain", ""),
                explain_if=it.get("explain_if"),
            )
        )
    return rules


def _parse_checklists(raw: dict) -> tuple[Checklist, ...]:
    out = []
    for c in raw.get("checklists", []):
        scopes = tuple(
            OtherScope(s["label"], s.get("clause", ""), _numbers(str(s["questions"])))
            for s in c.get("other_scopes", [])
        )
        out.append(
            Checklist(
                id=c["id"],
                agency=c["agency"],
                number=str(c.get("number", "")),
                title=c["title"],
                doc=c.get("doc", ""),
                questions=c.get("questions"),
                coverage=c.get("coverage", ""),
                applies_if=c.get("applies_if"),
                when=c.get("when", ""),
                when_if=c.get("when_if"),
                industry=c.get("industry", ""),
                other_scopes=scopes,
            )
        )
    return tuple(out)


def load_rulebook(rules_dir: str | Path) -> Rulebook:
    rules_dir = Path(rules_dir)
    profile_raw = yaml.safe_load((rules_dir / "profile.yaml").read_text(encoding="utf-8"))
    fields = _parse_fields(profile_raw)

    rules: list[Rule] = []
    versions = [str(profile_raw.get("version", ""))]
    batches: list[tuple[int, str, list[Rule]]] = []
    checklists: tuple[Checklist, ...] = ()
    for path in sorted(rules_dir.glob("*.yaml")):
        if path.name == "profile.yaml":
            continue
        if path.name == "checklists.yaml":
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            checklists = _parse_checklists(raw)
            versions.append(str(raw.get("version", "")))
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
    preds = [(r.id, r.applies_if) for r in rules] + [(c.id, c.applies_if) for c in checklists]
    preds += [(r.id, r.explain_if) for r in rules]
    preds += [(c.id, c.when_if) for c in checklists]
    preds += [(f.key, f.ask_if) for f in fields]
    for owner, pred in preds:
        for key in _predicate_fields(pred):
            if key not in field_keys:
                raise ValueError(f"{owner}: предикат ссылается на неизвестное поле {key!r}")

    known = {c.id for c in checklists}
    for r in rules:
        for cid, _ in r.refs:
            if checklists and cid not in known:
                raise ValueError(f"{r.id}: ссылка на неизвестный проверочный лист {cid!r}")

    return Rulebook(version=max(versions), fields=fields, rules=tuple(rules), checklists=checklists)


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
