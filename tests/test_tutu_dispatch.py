import asyncio

import pytest

from app.cart import service as cart_service
from app.cart.service import CartError
from app.planning import build, replan, validate


def test_live_build_uses_existing_track_a_entrypoint(monkeypatch):
    called = []

    async def fake(requirements, state):
        called.append((requirements, state))
        return [{"id": "b-live", "items": [], "total_rub": 0}]

    monkeypatch.setenv("TRAVEL_DATA_PROVIDER", "tutu")
    monkeypatch.setattr("app.providers.tutu.planner.build_tutu_bundles", fake)
    result = build.build_bundles({"origin": "MOW"}, {"version": 1})
    assert asyncio.run(result) == [{"id": "b-live", "items": [], "total_rub": 0}]
    assert called == [({"origin": "MOW"}, {"version": 1})]


def test_live_replan_dispatch_preserves_changes_contract(monkeypatch):
    called = []

    async def fake(state, changes):
        called.append((state, changes))
        return {"bundle": None, "input_changes": changes}

    monkeypatch.setenv("TRAVEL_DATA_PROVIDER", "tutu")
    monkeypatch.setattr("app.providers.tutu.planner.replan_tutu", fake)
    result = replan.replan({"dates": {"start": "2026-11-24"}}, {"selected_bundle_id": "b1"},
                           bundle_id="b1", changes={"upserts": [{"key": "pets", "value": {"kind": "dog"}}]})
    output = asyncio.run(result)
    assert output["input_changes"]["upserts"][0]["key"] == "pets"
    assert called[0][0] == {"selected_bundle_id": "b1"}


def test_live_checkout_validation_dispatches_read_only_checker(monkeypatch):
    async def fake(bundle, state):
        return [{"kind": "availability", "status": "block", "text": "not fresh"}]

    monkeypatch.setenv("TRAVEL_DATA_PROVIDER", "tutu")
    monkeypatch.setattr("app.providers.tutu.revalidation.validate_tutu_bundle", fake)
    result = validate.validate_bundle({"items": [{"provider": "tutu"}]}, {"version": 2})
    assert asyncio.run(result)[0]["status"] == "block"


def test_blocking_live_check_cannot_enter_cart(monkeypatch):
    async def fake(bundle, state):
        return [{"kind": "pets", "status": "block", "text": "pet rules unknown"}]

    monkeypatch.setenv("TRAVEL_DATA_PROVIDER", "tutu")
    monkeypatch.setattr("app.providers.tutu.revalidation.validate_tutu_bundle", fake)
    bundle = {"id": "b1", "items": [{"offer_id": "o1", "provider": "tutu", "total_rub": 10}],
              "total_rub": 10, "checks": []}
    with pytest.raises(CartError) as error:
        cart_service._check_bundle(bundle, {})
    assert error.value.code == "plan_blocked"


def test_live_scope_keeps_origin_city_and_uses_configured_destination():
    from app.providers.tutu.planner import TutuOriginOutsideRussia, TutuScopeError, _mapped_requirements, _origin_for_provider
    from app.planning.query import query_from_requirements
    from app.catalog.store import get_catalog

    mapped = _mapped_requirements({"origin": "Казань"}, None)
    assert mapped["origin"] == "Казань" and mapped["destination"] == "AER"
    assert _origin_for_provider(mapped) == "Казань"
    with pytest.raises(TutuOriginOutsideRussia):
        _origin_for_provider({"origin": "Стамбул"})
    with pytest.raises(TutuScopeError):
        _mapped_requirements({"origin": "Казань", "destination": "Дубай"}, None)

    requirements = {"origin": "Самара", "destination": "AER",
                    "dates": {"start": "2026-11-25", "end": "2026-12-02"}, "adults": 2}
    query, problems = query_from_requirements(get_catalog(), requirements, allow_external_origin=True)
    assert not problems and query is not None and query.origin_city == "Самара"


def test_ambiguous_origin_is_turned_into_user_clarification():
    from app.providers.tutu.planner import _origin_resolution_error, TutuOriginAmbiguous, TutuOriginUnavailable

    ambiguous = type("Upstream", (), {"upstream_message": "could not resolve origin='Самары': 2 places share this name"})()
    error = _origin_resolution_error(ambiguous, "Самары")
    assert isinstance(error, TutuOriginAmbiguous)
    unsupported = type("Upstream", (), {"upstream_message": "origin has no avia_id; answers this wording with «Москва» (MOW)"})()
    error = _origin_resolution_error(unsupported, "Пушкино")
    assert isinstance(error, TutuOriginUnavailable) and error.candidate == "Москва"


def test_cart_rejects_destination_outside_current_product_coverage():
    from app.cart.service import CartError

    with pytest.raises(CartError) as error:
        cart_service._check_bundle({"items": [{"total_rub": 10}], "total_rub": 10},
                                   {"requirements": [{"key": "destination", "value": "DXB"}]})
    assert error.value.code == "destination_not_covered"
