from pathlib import Path

import pytest

from app.engine import evaluate_all, load_rulebook, summary
from app.engine.applicability import evaluate
from app.engine.rules import Rule, Source

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"


@pytest.fixture(scope="module")
def book():
    return load_rulebook(RULES_DIR)


CAFE = {"activity": "cafe", "has_kitchen": True, "own_production": True, "seats": 35, "staff": 10, "alcohol": False}
COFFEE = {"activity": "coffee", "has_kitchen": False, "own_production": False, "seats": 0, "staff": 0, "alcohol": False}


def test_rulebook_loads(book):
    assert len(book.rules) >= 25
    assert {a for a, _ in book.agencies} == {"rospotrebnadzor", "rostrud", "mchs"}
    assert all(r.source.doc for r in book.rules)


def test_two_profiles_give_different_sets(book):
    cafe = summary(CAFE, book)
    coffee = summary(COFFEE, book)
    assert cafe["applicable"] > coffee["applicable"]
    # У кофейни без кухни и работников не должно быть трудовых требований и бракеража.
    coffee_ids = {v.rule.id for v in evaluate_all(COFFEE, book) if v.applicable}
    assert not any(i.startswith("rt-") and not i.startswith("rt-shop") for i in coffee_ids)
    assert "rpn-flow" not in coffee_ids and "rpn-brakerazh" not in coffee_ids
    # Но безусловные требования остаются.
    assert "rpn-temp-log" in coffee_ids
    assert "mchs-extinguishers" in coffee_ids


def test_reasons_are_human_readable(book):
    verdicts = {v.rule.id: v for v in evaluate_all(COFFEE, book)}
    v = verdicts["rpn-flow"]
    assert not v.applicable
    assert v.reasons == ("Кухня: нет кухни",)
    v = verdicts["rt-contracts"]
    assert v.reasons == ("Работники: Нет, работаю сам",)


def test_any_collects_all_failed_branches(book):
    v = {v.rule.id: v for v in evaluate_all(COFFEE, book)}["rpn-haccp"]
    assert not v.applicable
    assert set(v.reasons) == {"Кухня: нет кухни", "Собственное производство: нет производства"}


def test_shop_profile_uses_retail_rulebook(book):
    """Магазин получает розничный справочник (СП 2.3.6.3668-20), а требования СанПиН по общепиту к нему не применяются."""
    shop = {"activity": "shop", "has_kitchen": False, "own_production": False, "seats": 0, "staff": 3, "alcohol": True}
    ids = {v.rule.id for v in evaluate_all(shop, book) if v.applicable}
    assert any(i.startswith("rt-shop-") for i in ids)
    assert not any(i in ids for i in ("rpn-temp-log", "rpn-health-journal", "rpn-flow"))
    assert {"rpn-notice", "rpn-alcohol"} <= ids  # универсальные
    assert any(i.startswith("rt-") and not i.startswith("rt-shop") for i in ids)  # Роструд — тоже


def test_every_profile_field_influences_some_rule(book):
    from app.engine.rules import _predicate_fields
    used = set()
    for r in book.rules:
        used |= _predicate_fields(r.applies_if)
    for f in book.fields:
        if f.type == "text":
            continue
        assert f.key in used, f"поле {f.key} не влияет ни на одно требование"


def test_unconditional_rule():
    r = Rule("x", "a", "A", "t", "c", Source("d"), None, "once", "check", "low", 1)
    class B:  # минимальный rulebook
        fields = ()
    assert evaluate(r, {}, B()).applicable
