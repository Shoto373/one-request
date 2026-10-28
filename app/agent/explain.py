"""Evidence-backed destination claims; LLM may phrase facts, never add them."""
from __future__ import annotations

import asyncio
import os

import re
from typing import Any

from pydantic import BaseModel, Field

from app.agent.llm import LLMClient
from app.state.models import Claim


class EvidenceFact(BaseModel):
    ref: str
    value: Any
    label: str
    unit: str | None = None
    kind: str
    source: str
    source_refs: list[str] = Field(default_factory=list)
    synthetic: bool | None = None
    role: str = "fit"


class Explanation(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


class BundleExplanation(BaseModel):
    advantages: list[Claim] = Field(default_factory=list)
    tradeoffs: list[Claim] = Field(default_factory=list)
    risks: list[Claim] = Field(default_factory=list)



# Объяснения — только формулировка уже посчитанных фактов: если модель не уложилась,
# берём детерминированные утверждения, а не держим пользователя минутами.
EXPLAIN_TIMEOUT = float(os.getenv("LLM_EXPLAIN_TIMEOUT_SECONDS", "20"))

def _numbers(value: Any) -> set[str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        value = str(value)
    if isinstance(value, str):
        matches = re.findall(r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?", value)
        return {re.sub(r"[ \u00a0]", "", x).replace(",", ".") for x in matches}
    if isinstance(value, dict):
        out: set[str] = set()
        for nested in value.values():
            out.update(_numbers(nested))
        return out
    if isinstance(value, (list, tuple)):
        out: set[str] = set()
        for nested in value:
            out.update(_numbers(nested))
        return out
    return set()


def _validate_fact(raw: dict[str, Any], *, destination: dict[str, Any]) -> EvidenceFact | None:
    """Resolve known catalog refs independently; other refs need source metadata from A."""
    ref = raw.get("ref")
    if not isinstance(ref, str):
        return None
    climate = re.fullmatch(r"climate:([A-Z]{3}):(\d{1,2})\.([a-z_]+)", ref)
    if climate:
        from app.catalog.store import get_catalog
        city_id, month_num, field = climate.group(1), int(climate.group(2)), climate.group(3)
        catalog = get_catalog()
        climate_record = catalog.climate.get(city_id)
        if climate_record is None or not 1 <= month_num <= 12:
            return None
        month = climate_record.months[month_num - 1]
        if field not in {"air_temp_day", "air_temp_night", "sea_temp", "precip_mm", "rainy_days", "sun_hours"}:
            return None
        value = getattr(month, field)
        if value is None or (raw.get("value") is not None and float(raw["value"]) != float(value)):
            return None
        labels = {
            "air_temp_day": "Средний дневной максимум температуры воздуха",
            "air_temp_night": "Средний ночной минимум температуры воздуха",
            "sea_temp": "Средняя температура моря",
            "precip_mm": "Средние осадки за месяц",
            "rainy_days": "Среднее число дождливых дней за месяц",
            "sun_hours": "Средняя продолжительность солнца в день",
        }
        return EvidenceFact(
            ref=ref, value=value, label=labels[field], unit="°C" if "temp" in field else ("мм" if field == "precip_mm" else None),
            kind="climate_statistic", source=f"Климатическая статистика за {climate_record.period}",
            source_refs=climate_record.source_refs, role=raw.get("role", "fit"),
        )
    # Data outside the climate catalog must arrive with source and provenance in
    # A's tool result. Those are retained for the UI tooltip.
    if not raw.get("source") or not raw.get("source_refs") or "value" not in raw or not raw.get("label"):
        return None
    try:
        return EvidenceFact.model_validate(raw)
    except Exception:
        return None


def resolve_evidence(raw_facts: list[dict[str, Any]], *, destination: dict[str, Any] | None = None) -> tuple[list[EvidenceFact], dict[str, dict[str, Any]]]:
    facts = []
    for raw in raw_facts:
        if isinstance(raw, dict):
            fact = _validate_fact(raw, destination=destination or {})
            if fact:
                facts.append(fact)
    facts = list({fact.ref: fact for fact in facts}.values())
    return facts, {fact.ref: fact.model_dump(mode="json") for fact in facts}


def validate_claims(claims: list[Claim], facts: list[EvidenceFact]) -> bool:
    by_ref = {fact.ref: fact for fact in facts}
    for claim in claims:
        if not claim.refs or any(ref not in by_ref for ref in claim.refs):
            return False
        allowed: set[str] = set()
        for ref in claim.refs:
            allowed.update(_numbers(by_ref[ref].value))
        if not _numbers(claim.text).issubset(allowed):
            return False
    return True


async def explain_bundle_claims(llm: LLMClient, bundle: Any, requirements: list[Any]) -> tuple[BundleExplanation, dict[str, Any]]:
    """Let the LLM phrase bundle facts, then independently reject unsupported claims.

    Prices and feasibility are passed through from A's Offers and Checks. The
    model cannot introduce a number or a source ref that did not come from them.
    """
    offer_facts = []
    offer_refs = []
    check_source = "Tutu MCP provider checks" if any(getattr(offer, "provider", None) == "tutu" for offer in bundle.items) else "Track A validate_plan"
    by_ref: dict[str, EvidenceFact] = {}
    for offer in bundle.items:
        ref = f"offer:{offer.offer_id}"
        offer_refs.append(ref)
        data = offer.model_dump(mode="json")
        fact = EvidenceFact(
            ref=ref, value=data, label=offer.title or offer.item_id, kind="offer",
            source="Track A Offer", source_refs=offer.source_refs or [ref],
        )
        by_ref[ref] = fact
        offer_facts.append(fact.model_dump(mode="json"))
    check_facts = []
    for index, check in enumerate(bundle.checks):
        ref = f"check:{bundle.id}:{index}"
        fact = EvidenceFact(
            ref=ref, value={"kind": check.kind, "status": check.status, "text": check.text},
            label=check.kind, kind="plan_check", source=check_source,
            source_refs=check.refs or sorted(f"offer:{o.offer_id}" for o in bundle.items),
        )
        by_ref[ref] = fact
        check_facts.append(fact.model_dump(mode="json"))
    bundle_ref = f"bundle:{bundle.id}"
    by_ref[bundle_ref] = EvidenceFact(
        ref=bundle_ref, value={"id": bundle.id, "label": bundle.label, "total_rub": bundle.total_rub},
        label=bundle.label, kind="bundle_result", source="Track A build/replan result",
        source_refs=sorted(by_ref),
    )
    allowed_refs = {fact.ref for fact in by_ref.values()}
    requirement_facts = [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in requirements]
    system = f"""Write brief, evidence-backed bundle advantages, trade-offs and risks in Russian.
Use only the supplied Offer, Check and bundle facts. Return JSON with arrays advantages, tradeoffs, risks of {{text,refs}}.
Each claim needs one or more exact refs from the supplied data. Do not calculate, alter or estimate prices, durations,
or feasibility. Never present a warning option as a guaranteed service. Do not include unsupported numeric values.
Offers: {offer_facts}
Checks: {check_facts}
Bundle result: {by_ref[bundle_ref].model_dump(mode='json')}
User requirements: {requirement_facts}
Available refs: {sorted(allowed_refs)}"""
    raw = await asyncio.wait_for(llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": "Explain this selected travel bundle."}],
        BundleExplanation, fast=True,
    ), EXPLAIN_TIMEOUT)
    candidate = raw if isinstance(raw, BundleExplanation) else BundleExplanation.model_validate(raw)
    valid: dict[str, list[Claim]] = {}
    for name in ("advantages", "tradeoffs", "risks"):
        claims = getattr(candidate, name)
        accepted = []
        for claim in claims:
            if not claim.refs or any(ref not in allowed_refs for ref in claim.refs):
                continue
            allowed_numbers: set[str] = set()
            for ref in claim.refs:
                allowed_numbers.update(_numbers(by_ref[ref].value))
            if _numbers(claim.text).issubset(allowed_numbers):
                accepted.append(claim)
        valid[name] = accepted
    return BundleExplanation(**valid), {
        "step_type": "llm", "name": "explain_bundle", "input": {"bundle_id": bundle.id, "offer_refs": sorted(offer_refs)},
        "output": {key: [c.model_dump(mode="json") for c in value] for key, value in valid.items()}, "status": "ok",
    }


def deterministic_claims(facts: list[EvidenceFact]) -> list[Claim]:
    claims = []
    for fact in facts:
        value = fact.value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        shown = f"{value:,}".replace(",", " ") if isinstance(value, int) and abs(value) >= 10000 else value
        rendered = f"{shown}{' ' + fact.unit if fact.unit else ''}"
        label = fact.label[:1].upper() + fact.label[1:]
        if fact.kind == "climate_statistic":
            text = f"В среднем за месяц: {fact.label[:1].lower() + fact.label[1:]} — {rendered}."
        elif fact.synthetic:
            text = f"Ориентировочно: {fact.label[:1].lower() + fact.label[1:]} — {rendered}."
        else:
            text = f"{label} — {rendered}."
        claims.append(Claim(text=text, refs=[fact.ref]))
    return claims


async def explain_destination(
    llm: LLMClient, destination: dict[str, Any], *, max_steps: int = 1,
) -> tuple[list[Claim], list[Claim], dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (why, risks, resolvable source map, trace) after evidence validation."""
    facts, sources = resolve_evidence(destination.get("evidence", destination.get("facts", [])), destination=destination)
    if not facts:
        return [], [], {}, {"step_type": "tool_result", "name": "explain", "input": {"destination_id": destination.get("id")}, "output": {"claims": []}, "status": "ok"}
    prompt_facts = [fact.model_dump(mode="json") for fact in facts]
    try:
        response = await asyncio.wait_for(llm.chat_json(
            [
                {"role": "system", "content": (
                    "Write short Russian recommendation claims using ONLY supplied facts. "
                    "Return JSON {claims:[{text,refs}]}. Every claim must cite one or more exact supplied refs. "
                    "Do not add or change numbers, prices, weather, visa facts, amenities, or source refs. "
                    "A climate_statistic is historical/period statistics, never a forecast. "
                    "An estimate is not the price of a specific bundle. A trip total is not an all-inclusive hotel tariff. "
                    "Preserve synthetic labels. No reasoning or unsupported prose."
                )},
                {"role": "user", "content": str(prompt_facts)},
            ], Explanation, fast=True,
        ), EXPLAIN_TIMEOUT)
        all_claims = response.claims
        if not validate_claims(all_claims, facts):
            raise ValueError("explanation claims contain an unknown ref or unsupported number")
        trace = {"step_type": "llm", "name": "explain", "input": {"facts": prompt_facts}, "output": response.model_dump(mode="json"), "status": "ok"}
    except Exception as exc:
        all_claims = deterministic_claims(facts)
        trace = {"step_type": "tool_result", "name": "explain", "input": {"facts": prompt_facts},
                 "output": {"claims": [c.model_dump(mode="json") for c in all_claims], "fallback": type(exc).__name__}, "status": "fallback"}
    return (
        [claim for claim in all_claims if any(sources[r].get("role") != "risk" for r in claim.refs)],
        [claim for claim in all_claims if any(sources[r].get("role") == "risk" for r in claim.refs)],
        sources,
        trace,
    )
