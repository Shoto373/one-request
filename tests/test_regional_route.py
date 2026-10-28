"""Regression: «Рязань → Питер» must not invent flights; city names are shown in full."""

from __future__ import annotations

from app.agent.requirements import ParsedRequest, validate_extraction
from app.catalog.store import get_catalog
from app.planning.build import build_bundles
from app.planning.query import canonical_city_name, resolve_city
from app.state.models import Requirement

REQ = {"origin": "Рязань", "destination": "Питер", "dates": {"start": "2026-11-06", "end": "2026-11-08"}, "adults": 1}


def _state(req: dict) -> dict:
    return {"requirements": [{"key": k, "value": v, "kind": "hard"} for k, v in req.items()]}


def test_city_without_airport_gets_no_invented_flights() -> None:
    cat = get_catalog()
    ryazan = resolve_city(cat, "Рязань")
    assert ryazan and ryazan.startswith("W_")
    invented = [f for f in cat.flights.values() if ryazan in (f.origin_city, f.dest_city)]
    assert invented == []
    generated = [f for f in cat.flights.values() if "ref:world_resolver" in f.source_refs]
    assert generated and not any(f.carrier == "FZ" for f in generated)  # no Flydubai default on Russian routes


def test_ryazan_to_piter_goes_through_moscow_by_train() -> None:
    bundles = build_bundles(REQ, _state(REQ))
    assert bundles
    for b in bundles:
        transport = [o for o in b["items"] if o["kind"] in ("flight", "train")]
        out, back = transport[0], transport[-1]
        assert out["variant"].get("intermodal") and back["variant"].get("intermodal")
        assert out["variant"]["dest_city"] == "LED" and back["variant"]["origin_city"] == "LED"
        assert "Flydubai" not in (out.get("title") or "") and "Flydubai" not in (back.get("title") or "")
        legs = out["variant"]["legs"]
        assert legs[0]["label"].startswith("Трансфер") and legs[0]["from"] == "Рязань"
        assert legs[1]["mode"] in ("train", "flight")
        hotel = next(o for o in b["items"] if o["kind"] == "hotel")
        assert "Санкт-Петербург" in hotel["title"] or hotel["variant"].get("city_id") == "LED"
        # The ground leg reaches the hub before the train/flight leaves.
        assert legs[0]["arrival_at"] <= legs[1]["departure_at"]


def test_canonical_city_names() -> None:
    cat = get_catalog()
    assert canonical_city_name(cat, "Питер") == "Санкт-Петербург"
    assert canonical_city_name(cat, "мск") == "Москва"
    assert canonical_city_name(cat, "Екб") == "Екатеринбург"
    assert canonical_city_name(cat, "Анталию") == "Анталья"
    assert canonical_city_name(cat, "абракадабра") is None


def test_extracted_city_value_is_canonical_but_quote_is_the_users() -> None:
    parsed = ParsedRequest(requirements=[
        Requirement(key="origin", value="Рязань", kind="hard", source="user_text", quote="Рязань"),
        Requirement(key="destination", value="Питер", kind="hard", source="user_text", quote="Питер"),
    ])
    out = {r.key: r for r in validate_extraction(parsed, "Рязань Питер").requirements}
    assert out["destination"].value == "Санкт-Петербург" and out["destination"].quote == "Питер"
    assert out["origin"].value == "Рязань"


def test_display_names_do_not_depend_on_the_llm() -> None:
    """GigaChat often keeps the user's spelling: abbreviations are expanded deterministically."""
    from app.planning.query import display_city_name

    cat = get_catalog()
    assert display_city_name(cat, "LA") == "Лос-Анджелес"
    assert display_city_name(cat, "nyc") == "Нью-Йорк"
    assert display_city_name(cat, "нск") == "Новосибирск"
    assert display_city_name(cat, "рязань") == "Рязань"
    assert display_city_name(cat, "ростов-на-дону") == "Ростов-на-Дону"
    assert display_city_name(cat, "Кёниг") == "Калининград"
