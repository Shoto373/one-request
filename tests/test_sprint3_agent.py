from datetime import date
from uuid import uuid4

import pytest

from app.agent.explain import BundleExplanation, explain_bundle_claims
from app.agent.orchestrator import InvalidAgentCommand, Orchestrator, ParsedRequest
from app.state.models import Bundle, Check, Claim, Requirement, TripState
from app.catalog.models import Offer


class FakeLLM:
    model_fast = "test"

    def __init__(self, *responses):
        self.responses = list(responses)

    async def chat_json(self, *_args, **_kwargs):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def req(key, value, kind="hard"):
    return Requirement(key=key, value=value, kind=kind, source="user_text")


def offer(offer_id="hotel-old", start="2026-11-10", end="2026-11-17", total=7000, item_id="hotel-1"):
    return Offer(
        offer_id=offer_id, kind="hotel", item_id=item_id, title="Тестовый отель",
        unit_price_rub=total, price_unit="per_trip", units=1,
        total_rub=total, times={"start": f"{start}T14:00:00+03:00", "end": f"{end}T12:00:00+03:00"},
        synthetic=True, source_refs=["fixture:hotel"],
    )


def bundle(offer_value=None, *, checks=None, bid="b1", label="дешевле"):
    item = offer_value or offer()
    return Bundle(id=bid, label=label, items=[item], total_rub=item.total_rub, checks=checks or [])


def selected_state(*, bundle_value=None):
    selected = bundle_value or bundle()
    return TripState(
        session_id=uuid4(), stage="assemble", selected_destination_id="AER",
        selected_bundle_id=selected.id, bundles=[selected],
        requirements=[req("origin", "Москва"), req("destination", "AER"),
                      req("dates", {"start": "2026-11-10", "end": "2026-11-17"}), req("adults", 2)],
    )


def replan_result(*, total=7200, checks=None):
    updated = bundle(offer("hotel-new", "2026-11-12", "2026-11-19", total), checks=checks)
    return {"bundles": [updated], "kept": ["hotel:hotel-1"], "replaced": ["offer:hotel-old→hotel-new"],
            "reasons": ["Старое ценовое предложение не покрывает новые даты"], "price_delta_rub": total - 7000}


@pytest.mark.asyncio
async def test_relative_dates_replan_selected_bundle_and_remove_old_offers():
    calls = []

    async def replan(state, diff):
        calls.append((state, diff))
        return replan_result()

    state = selected_state()
    result, reply, trace = await Orchestrator(FakeLLM(ParsedRequest()), replanner=replan).prepare_turn(state, "На два дня позже")
    assert len(calls) == 1
    assert calls[0][0].bundles[0].id == "b1"  # A receives the source selection.
    upserts = {item["key"]: item["value"] for item in calls[0][1]["upserts"]}
    assert upserts["dates"] == {"start": "2026-11-12", "end": "2026-11-19"}
    retained = {r.key: r.value for r in calls[0][0].requirements}
    assert retained["origin"] == "Москва" and retained["adults"] == 2
    assert result.bundles[0].items[0].offer_id == "hotel-new"
    assert result.bundles[0].items[0].times.start.date().isoformat() == "2026-11-12"
    assert result.bundles[0].items[0].times.end.date().isoformat() == "2026-11-19"
    assert result.history[-1].kept == ["hotel:hotel-1"]
    assert result.history[-1].replaced == ["offer:hotel-old→hotel-new"]
    assert result.history[-1].price_delta_rub == 200
    assert result.history[-1].reasons == ["Старое ценовое предложение не покрывает новые даты"]
    assert "200 ₽" in reply
    assert [step["name"] for step in trace if step["name"].startswith("replan")] == ["replan", "replan_result"]


@pytest.mark.asyncio
async def test_dates_and_dog_require_weight_then_replan_checks_pet_all_parts():
    calls = []
    pet_check = {"kind": "pets", "status": "ok", "text": "Проверены перевозчик, отель и трансферы.",
                 "refs": ["offer:hotel-new"]}

    async def replan(state, diff):
        calls.append((state, diff))
        return replan_result(checks=[pet_check])

    state = selected_state()
    first, question, _ = await Orchestrator(FakeLLM(ParsedRequest()), replanner=replan).prepare_turn(
        state, "На два дня позже и с собакой",
    )
    assert not calls
    assert first.stage == "clarify" and first.open_questions[0].key == "pet_weight_kg"
    assert not first.bundles and first.pending_replan is not None

    answer = ParsedRequest(requirements=[Requirement(key="pet_weight_kg", value=8, kind="hard", source="user_text", quote="8 кг")])
    final, final_reply, _ = await Orchestrator(FakeLLM(answer), replanner=replan).prepare_turn(first, "8 кг")
    diff = {r["key"]: r["value"] for r in calls[0][1]["upserts"]}
    assert diff["dates"] == {"start": "2026-11-12", "end": "2026-11-19"}
    assert diff["pets"] == {"kind": "dog"} and diff["pet_weight_kg"] == 8
    assert final.bundles[0].checks[0].status == "ok"
    assert "перепроверен" in final_reply


@pytest.mark.asyncio
async def test_budget_less_without_amount_asks_and_does_not_replan_or_change_requirements():
    called = False

    async def replan(*_):
        nonlocal called
        called = True
        return replan_result()

    state = selected_state()
    result, reply, _ = await Orchestrator(FakeLLM(ParsedRequest()), replanner=replan).prepare_turn(state, "Сделай бюджет меньше")
    assert result.open_questions[0].key == "budget_total_rub"
    assert "суммы" in reply.lower()
    assert not called
    assert result.requirements == state.requirements
    assert result.bundles[0].id == "b1"


@pytest.mark.asyncio
async def test_chat_and_ui_requirement_edit_send_the_same_typed_diff_to_a():
    diffs = []

    async def replan(_state, diff):
        diffs.append(diff)
        return replan_result()

    chat_state = selected_state()
    await Orchestrator(FakeLLM(ParsedRequest()), replanner=replan).prepare_turn(chat_state, "На два дня позже")

    ui_state = selected_state()
    await Orchestrator(FakeLLM(), replanner=replan).prepare_command(
        ui_state, "edit_requirement", {"key": "dates", "value": {"start": "2026-11-12", "end": "2026-11-19"}, "kind": "hard"},
    )
    assert diffs[0] == diffs[1]


@pytest.mark.asyncio
async def test_replan_error_does_not_mutate_or_replace_the_committed_state():
    async def fail(*_):
        raise RuntimeError("A unavailable")

    state = selected_state()
    old_payload = state.model_dump(mode="json")
    with pytest.raises(RuntimeError, match="A unavailable"):
        await Orchestrator(FakeLLM(ParsedRequest()), replanner=fail).prepare_turn(state, "На два дня позже")
    assert state.model_dump(mode="json") == old_payload


@pytest.mark.asyncio
async def test_no_feasible_replan_persists_new_hard_budget_and_archives_old_bundle():
    async def no_plan(_state, diff):
        assert any(x["key"] == "budget_total_rub" and x["kind"] == "hard" for x in diff["upserts"])
        return {"bundles": [], "kept": [], "replaced": ["offer:hotel-old"],
                "reasons": ["Минимальная стоимость выше лимита"], "price_delta_rub": None}

    parsed = ParsedRequest(requirements=[
        Requirement(key="budget_total_rub", value=1000, kind="hard", source="user_text", quote="1000 ₽ на всю поездку"),
        Requirement(key="budget_scope", value="trip", kind="hard", source="user_text", quote="на всю поездку"),
    ])
    state, reply, trace = await Orchestrator(FakeLLM(parsed), replanner=no_plan).prepare_turn(selected_state(), "Бюджет до 1000 ₽ на всю поездку")
    assert next(r for r in state.requirements if r.key == "budget_total_rub").kind == "hard"
    assert not state.bundles and state.selected_bundle_id is None
    assert any(step["name"] == "replan_result" for step in trace), trace
    assert state.history[-1].previous_bundle["id"] == "b1"
    assert "только в истории" in reply


@pytest.mark.asyncio
async def test_unknown_bundle_id_is_rejected_and_actual_bundle_count_is_preserved():
    state = selected_state()
    orch = Orchestrator(FakeLLM())
    with pytest.raises(InvalidAgentCommand):
        await orch.prepare_command(state, "select_bundle", {"bundle_id": "fabricated"})

    parsed = ParsedRequest(requirements=[
        Requirement(key="origin", value="Москва", kind="hard", source="user_text", quote="Из Москвы"),
        Requirement(key="destination", value="Сочи", kind="hard", source="user_text", quote="в Сочи"),
        Requirement(key="dates", value={"start": "2026-11-10", "end": "2026-11-17"}, kind="hard", source="user_text", quote="10–17 ноября"),
        Requirement(key="adults", value=2, kind="hard", source="user_text", quote="двое взрослых"),
    ])
    two = [bundle(offer("one", total=7000), bid="b1"), bundle(offer("two", total=9000), bid="b2", label="гибче отмена")]
    result, _, _ = await Orchestrator(FakeLLM(parsed), bundle_builder=lambda *_: two, today=lambda: date(2026, 10, 23)).prepare_turn(
        TripState(session_id=uuid4()), "Из Москвы в Сочи 10–17 ноября, двое взрослых",
    )
    assert [b.id for b in result.bundles] == ["b1", "b2"]
    assert result.stage == "compare"
    assert all(b.advantages and all(claim.refs for claim in b.advantages) for b in result.bundles)


@pytest.mark.asyncio
async def test_request_checkout_requires_real_nonblocking_validation():
    state = selected_state()
    blocked = Orchestrator(FakeLLM(), bundle_validator=lambda *_: [
        {"kind": "pets", "status": "block", "text": "Трансфер не принимает собаку", "refs": ["offer:hotel-old"]},
    ])
    result, reply, _ = await blocked.prepare_command(state, "request_checkout", {})
    assert result.stage == "assemble" and result.bundles[0].checks[0].status == "block"
    assert "Трансфер не принимает собаку" in reply

    ready = Orchestrator(FakeLLM(), bundle_validator=lambda *_: [
        {"kind": "availability", "status": "ok", "text": "Предложения доступны", "refs": ["offer:hotel-old"]},
        {"kind": "budget", "status": "warn", "text": "Есть предупреждение", "refs": ["offer:hotel-old"]},
    ])
    result, _, _ = await ready.prepare_command(state, "request_checkout", {})
    assert result.stage == "checkout"
    assert result.bundles[0].checks[1].status == "warn"


@pytest.mark.asyncio
async def test_llm_bundle_claims_keep_only_existing_refs_and_supported_numbers():
    facts = bundle(checks=[Check(id="check:b1:0", kind="budget", status="ok", text="Итог 7000 ₽ в лимите")])
    llm = FakeLLM(BundleExplanation(advantages=[
        Claim(text="Общая стоимость 7 000 ₽", refs=["bundle:b1"]),
        Claim(text="Общая стоимость 9 000 ₽", refs=["bundle:b1"]),
        Claim(text="Сомнительное преимущество", refs=["offer:missing"]),
    ]))
    explained, step = await explain_bundle_claims(llm, facts, [req("budget_total_rub", 10000)])
    assert [c.text for c in explained.advantages] == ["Общая стоимость 7 000 ₽"]
    assert explained.advantages[0].refs == ["bundle:b1"]
    assert step["name"] == "explain_bundle" and step["status"] == "ok"

