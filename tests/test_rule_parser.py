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


@pytest.mark.asyncio
async def test_offline_turn_reaches_bundles():
    """Без ключа весь путь до комплектов работает на настоящем подборе трека A."""
    orch = Orchestrator(LLMClient(api_key="", base_url="https://example.invalid/v1", model="x"), now=NOW)
    state, reply, trace = await orch.prepare_turn(
        TripState(session_id=uuid4()), "Из Москвы в Сочи 10–17 ноября 2026, двое взрослых")
    assert trace[0]["name"] == "parse_request_rules"
    assert state.bundles, reply
