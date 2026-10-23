"""Evidence-backed destination claims; LLM may phrase facts, never add them."""
from __future__ import annotations

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


def _numbers(value: Any) -> set[str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        value = str(value)
    if isinstance(value, str):
        return {x.replace(",", ".") for x in re.findall(r"\d+(?:[.,]\d+)?", value)}
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


def deterministic_claims(facts: list[EvidenceFact]) -> list[Claim]:
    claims = []
    for fact in facts:
        value = fact.value
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        rendered = f"{value}{' ' + fact.unit if fact.unit else ''}"
        qualifier = "Климатическая статистика" if fact.kind == "climate_statistic" else "По данным подбора"
        if fact.synthetic:
            qualifier = "Сгенерированные условия; " + qualifier.lower()
        claims.append(Claim(text=f"{qualifier}: {fact.label.lower()} — {rendered}.", refs=[fact.ref]))
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
        response = await llm.chat_json(
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
        )
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
