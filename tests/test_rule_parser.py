"""Офлайн-режим: разбор запроса правилами, когда LLM нет или провайдер упал."""
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.agent.llm import LLMClient
from app.agent.orchestrator import Orchestrator
from app.agent.rule_parser import rule_parse
from app.state.models import OpenQuestion, TripState

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))


def parsed(text, state=None):
    p = rule_parse(text, state or TripState(session_id=uuid4()), NOW)
    return {r.key: r for r in p.requirements}


def test_exact_trip_with_dog():
    r = parsed("Из Москвы в Сочи 10–17 ноября, двое взрослых и собака 8 кг")
    assert r["origin"].value == "Москва" and r["destination"].value == "Сочи"
    assert r["dates"].value == {"start": "2026-11-10", "end": "2026-11-17"}
    assert r["adults"].value == 2 and r["pets"].value == {"kind": "dog"} and r["pet_weight_kg"].value == 8
    # цитаты — дословные подстроки сообщения
    assert all(x.quote in "Из Москвы в Сочи 10–17 ноября, двое взрослых и собака 8 кг" for x in r.values() if x.quote)


def test_vague_request_keeps_preferences_soft():
    r = parsed("Хочу к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов")
    assert r["travel_month"].value == 11
    assert r["budget_preference"].kind == "soft" and r["travel_time_preference"].value == "short"
    assert r["children_count"].value == 1 and "adults" not in r and "destination" not in r


def test_english_and_strict_budget():
    r = parsed("Trip from Saint Petersburg to Istanbul, November 10-14, two adults, budget strictly 120000 rub")
    assert r["origin"].value == "Санкт-Петербург" and r["destination"].value == "Стамбул"
    assert r["adults"].value == 2 and r["budget_total_rub"].value == 120000 and r["budget_total_rub"].kind == "hard"


def test_trip_word_is_not_train():
    r = parsed("Из Москвы в Сочи 10–17 ноября, строго до 100 000 рублей на всю поездку")
    assert "transport" not in r and r["budget_scope"].value == "trip"
    assert parsed("Из Москвы в Казань на выходные поездом")["transport"].value == "train"


def test_short_answer_maps_to_open_question():
    state = TripState(session_id=uuid4(), open_questions=[OpenQuestion(key="origin", question="Откуда?", why="")])
    assert parsed("Питер", state)["origin"].value == "Санкт-Петербург"


def test_origin_city_is_not_limited_to_demo_city_list():
    request = parsed("Из Казани в Сочи на неделю, двое взрослых")
    assert request["origin"].value == "Казань"
    state = TripState(session_id=uuid4(), open_questions=[OpenQuestion(key="origin", question="Из какого города?", why="")])
    clarified = parsed("Самары, Самарская область", state)
    assert clarified["origin"].value == "Самары, Самарская область"


def test_missing_origin_is_not_inferred_as_moscow():
    request = parsed("В Сочи на неделю, двое взрослых")
    assert "origin" not in request


@pytest.mark.asyncio
async def test_offline_turn_reaches_bundles():
    """Без ключа весь путь до комплектов работает на настоящем подборе трека A."""
    orch = Orchestrator(LLMClient(api_key="", base_url="https://example.invalid/v1", model="x"), now=NOW)
    state, reply, trace = await orch.prepare_turn(
        TripState(session_id=uuid4()), "Из Москвы в Сочи 10–17 ноября 2026, двое взрослых")
    assert trace[0]["name"] == "parse_request_rules"
    assert state.bundles, reply


def test_unknown_requirement_without_quote_does_not_crash():
    """Модели присылают unknown с quote=null — раньше это роняло весь ход TypeError."""
    from app.agent.requirements import ParsedRequest, validate_extraction
    from app.state.models import Requirement

    raw = ParsedRequest(requirements=[Requirement(key="dates", value=None, kind="unknown", source="agent", quote=None)])
    assert validate_extraction(raw, "Хочу в Сочи").requirements[0].key == "dates"


def test_za_amount_is_a_budget_cap():
    """«за 30 000» — предел бюджета: невыполнимый запрос должен дать честный отказ, а не комплекты за 350 000."""
    r = parsed("Неделя в Дубае на троих за 30 000")
    assert r["budget_total_rub"].value == 30000 and r["budget_total_rub"].kind == "hard"
    assert parsed("Сочи примерно за 80 000")["budget_total_rub"].kind == "soft"


def test_age_answer_wins_over_open_duration_question():
    state = TripState(session_id=uuid4(), open_questions=[OpenQuestion(key="duration_nights", question="?", why="")])
    r = parsed("5 лет", state)
    assert r["children_ages"].value == [5] and "duration_nights" not in r


def test_negated_destination_selects_affirmative_destination():
    r = parsed("не хочу в казань хочу на рождество в прагу")
    assert r["destination"].value == "Прага"
    assert r["dates"].value == {"start": "2026-12-30", "end": "2027-01-06"}


def test_versailles_and_luxury_preferences():
    r1 = parsed("хочу отель в версале")
    assert r1["room_preference"].value == "Версаль"

    r2 = parsed("ошибс хочу самый дорог 5 звездочный отель в центре парижа")
    assert r2["destination"].value == "Париж"
    assert r2["lodging_stars"].value == 5
    assert r2["budget_preference"].value == "luxury"
    assert r2["room_preference"].value == "центр"


def test_diverse_hotels_removes_star_constraints():
    p = rule_parse("предложи разные варинаты отелель", TripState(session_id=uuid4()), NOW)
    assert "lodging_stars" in p.remove_keys
