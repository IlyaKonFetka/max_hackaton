from pathlib import Path

import pytest

from app.engine import evaluate_all, funnel, load_rulebook, summary
from app.engine.applicability import evaluate
from app.engine.rules import Rule, Source, parse_refs

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"


@pytest.fixture(scope="module")
def book():
    return load_rulebook(RULES_DIR)


CAFE = {"activity": "cafe", "has_kitchen": True, "own_production": True, "seats": 35, "staff": 10, "alcohol": False,
        "services": ["drinks_machine", "fryer"]}
COFFEE = {"activity": "coffee", "has_kitchen": False, "own_production": False, "seats": 0, "staff": 0, "alcohol": False}


def ids(profile, book):
    return {v.rule.id for v in evaluate_all(profile, book) if v.applicable}


def test_rulebook_loads(book):
    assert len(book.rules) >= 70
    assert {a for a, _ in book.agencies} == {"rospotrebnadzor", "rostrud", "mchs", "alcohol"}
    assert all(r.source.doc for r in book.rules)
    assert len(book.checklists) == 6 + 80 + 27  # приказы № 808 РПН, № 20 Роструда, № 78 МЧС


def test_food_checklist_fully_covered(book):
    """Каждый из 124 вопросов листа общепита разобран: относится к требованию или к «другому виду деятельности»."""
    cl = next(c for c in book.checklists if c.id == "rpn-808-2")
    covered: set[int] = set()
    for r in book.rules:
        for cid, qs in r.refs:
            if cid == cl.id:
                covered |= set(qs)
    for sc in cl.other_scopes:
        covered |= set(sc.questions)
    assert covered == set(range(1, cl.questions + 1))


def test_refs_parsing():
    assert parse_refs("Приказ № 808, прил. № 2, вопросы 2–13, 21") == (("rpn-808-2", tuple(range(2, 14)) + (21,)),)
    assert parse_refs("Приказ Роструда № 20, приложения 6 «Режим» и 7 «Отдых»") == (("rt-6", ()), ("rt-7", ()))
    assert parse_refs("Приказ МЧС России от 09.02.2022 № 78, форма 1, вопросы 152–154") == (("mchs-1", (152, 153, 154)),)
    assert parse_refs("Приказ № 808, прил. № 2 — для общедоступного общепита отдельного вопроса нет") == ()


def test_two_profiles_give_different_sets(book):
    assert summary(CAFE, book)["applicable"] > summary(COFFEE, book)["applicable"]
    coffee = ids(COFFEE, book)
    # У кофейни без кухни и работников нет трудовых требований и поточности
    assert not any(i.startswith("rt-") and not i.startswith("rt-shop") for i in coffee)
    assert "rpn-flow" not in coffee and "rpn-brakerazh" not in coffee
    # Производственный контроль и технологические карты нужны и кофейне: напитки — тоже продукция
    assert {"rpn-haccp", "rpn-tech-docs", "rpn-temp-log", "mchs-extinguishers"} <= coffee


def test_reasons_are_human_readable(book):
    verdicts = {v.rule.id: v for v in evaluate_all(COFFEE, book)}
    assert verdicts["rpn-flow"].reasons == ("Кухня: нет кухни",)
    assert verdicts["rt-contracts"].reasons == ("Работники: Нет, работаю сам",)
    # Множественный выбор: причина говорит, чего нет
    assert verdicts["rpn-fryer"].reasons == ("Нет фритюра",)


def test_any_collects_all_failed_branches(book):
    v = {v.rule.id: v for v in evaluate_all(COFFEE, book)}["rpn-dishwashing"]
    assert not v.applicable
    assert set(v.reasons) == {"Кухня: нет кухни", "Посадочные места: Нет, только навынос"}


def test_services_switch_rules_on(book):
    assert "rpn-fryer" in ids(CAFE, book) and "rpn-machines" in ids(CAFE, book)
    assert "rpn-delivery" not in ids(CAFE, book)
    assert "rpn-delivery" in ids({**CAFE, "services": ["delivery"]}, book)


def test_other_domain_is_separated(book):
    """Требования для магазинов у кафе не попадают в обычное «не применимо», а считаются другой отраслью."""
    by_id = {v.rule.id: v for v in evaluate_all(CAFE, book)}
    assert by_id["rt-shop-docs"].other_domain == "Магазины"
    # Бракераж не прячется: владельцу кафе важно узнать, что по новому СанПиН он не обязателен, и почему
    assert by_id["rpn-brakerazh"].other_domain == "" and "п. 56, 64" in by_id["rpn-brakerazh"].reasons[0]
    assert by_id["rpn-delivery"].other_domain == ""  # доставку кафе может начать — это обычное «не применимо»


def test_new_sanpin_leaves_journals_only_for_organisations(book):
    canteen = {**CAFE, "activity": "canteen", "seats": 80}
    org = {**canteen, "activity": "canteen_org"}
    for rid in ("rpn-brakerazh", "rpn-daily-samples", "rpn-line-thermometers"):
        assert rid not in ids(canteen, book)
        assert rid in ids(org, book)


def test_shop_profile_uses_retail_rulebook(book):
    """Магазин получает розничный справочник, требования СанПиН по общепиту к нему не применяются."""
    shop = {"activity": "shop", "seats": 0, "staff": 3, "alcohol": True}
    got = ids(shop, book)
    assert any(i.startswith("rt-shop-") for i in got)
    assert not any(i in got for i in ("rpn-temp-log", "rpn-health-journal", "rpn-flow", "rpn-haccp"))
    assert {"rpn-notice", "alc-shop", "rt-contracts"} <= got


def test_funnel_counts_add_up(book):
    f = funnel(CAFE, book)
    assert f["checklists_total"] == 113
    assert f["checklists_yes"] + f["checklists_maybe"] < f["checklists_total"]
    d = next(x for x in f["detailed"] if x["id"] == "rpn-808-2")
    assert d["applicable"] + d["not_applicable"] + d["other_domain"] == 124
    assert d["other_domain"] == 15  # бортовое питание (14) и вагоны-рестораны (1)
    rt = next(a for a in f["agencies"] if a["agency"] == "rostrud")
    assert rt["total"] == 80 and rt["yes"] > 0
    # Без работников ни один лист Роструда не относится
    assert next(a for a in funnel(COFFEE, book)["agencies"] if a["agency"] == "rostrud")["yes"] == 0


def test_every_profile_field_influences_some_rule(book):
    from app.engine.rules import _predicate_fields

    used = set()
    for r in book.rules:
        used |= _predicate_fields(r.applies_if)
    for f in book.fields:
        if f.type == "text":
            continue
        assert f.key in used, f"поле {f.key} не влияет ни на одно требование"


def test_every_service_option_influences_some_rule(book):
    import json

    raw = json.dumps([r.applies_if for r in book.rules] + [c.applies_if for c in book.checklists], ensure_ascii=False)
    for o in book.field("services").options:
        assert f'"has": "{o.value}"' in raw, f"вариант {o.value} ни на что не влияет"


def test_unconditional_rule():
    r = Rule("x", "a", "A", "t", "c", Source("d"), None, "once", "check", "low", 1)

    class B:  # минимальный rulebook
        fields = ()

    assert evaluate(r, {}, B()).applicable


def test_testdata_profiles_match_engine(book):
    """testdata/profiles.json — демо-профили из README и DATA-API; цифры в документации должны совпадать с движком."""
    import json

    data = json.loads((RULES_DIR.parent / "testdata" / "profiles.json").read_text(encoding="utf-8"))
    assert data["total_rules"] == len(book.rules)
    for p in data["profiles"]:
        s = summary(p["profile"], book)
        e = p["expected"]
        assert s["applicable"] == e["applicable"], p["id"]
        assert s["by_agency"] == e["by_agency"], p["id"]
        assert s["funnel"]["checklists_yes"] == e["checklists_yes"], p["id"]
        if "rpn_808_2" in e:
            d = s["funnel"]["detailed"][0]
            assert d["applicable"] == e["rpn_808_2"]["applicable"], p["id"]
