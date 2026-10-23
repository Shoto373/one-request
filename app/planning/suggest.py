"""Orchestrator contract for destination search (track B: app/agent/destination_search.py).

    suggest_destinations(requirements, state) -> {destinations, alternatives, notes, required_inputs}

- ``destinations``: only options with a verified feasible bundle (``feasible: true``), each with
  ``evidence`` facts that B resolves for explanations and tooltips. Climate facts carry only
  ``ref/value/role`` (B re-reads them from the catalog); other facts carry their provenance.
- ``alternatives``: minimal changes when nothing fits, confirmed by a real build. Each has a stable
  ``id``, ``label``, exact ``changes`` in B's requirement keys and resolvable ``refs``/``evidence``.
  Nothing is applied here; hard requirements change only after the user accepts.
- ``required_inputs``: what is missing to search at all (``origin``, ``travel_window``).
"""

from __future__ import annotations

from typing import Any

from app.catalog.store import Catalog, get_catalog
from app.planning.alternatives import Change, minimal_changes
from app.planning.destinations import (
    closest_trip_query,
    destination_query_from_requirements,
    suggest_destinations_for,
)
from app.planning.query import _pick, merged_requirements, query_from_requirements, resolve_city
from app.tools.climate import climate_ref

TRIP_SOURCE = "Расчёт build_bundles по каталогу (условия сгенерированы)"
VISA_SOURCE = "Справочник визовых правил для граждан РФ"
# Keys B accepts in alternative changes (app/agent/orchestrator.py, _validated_alternatives).
B_CHANGE_KEYS = {"origin", "destination", "dates", "travel_month", "duration_nights", "budget_total_rub",
                 "budget_per_person_rub", "transport", "max_stops"}


def _offer_refs(bundle: dict[str, Any]) -> list[str]:
    return [f"offer:{o['offer_id']}" for o in bundle.get("items", [])]


def _destination_evidence(cat: Catalog, option: dict[str, Any], month: int) -> list[dict[str, Any]]:
    city_id = option["id"]
    climate = option.get("climate") or {}
    facts: list[dict[str, Any]] = []
    for field in ("air_temp_day", "air_temp_night", "sea_temp", "rainy_days"):
        value = climate.get(field)
        if value is None:
            continue
        role = "fit"
        if field == "rainy_days":
            role = "risk" if value >= 8 else "fit"
        if field == "sea_temp" and value < 21:
            role = "risk"
        facts.append({"ref": climate_ref(city_id, month, field), "value": value, "role": role})
    bundle = option["bundle"]
    refs = _offer_refs(bundle)
    facts.append({
        "ref": f"trip:{city_id}:total_rub", "value": option["est_total_rub"],
        "label": f"Итоговая цена поездки на весь состав ({option['nights']} ноч.: дорога, трансферы, отель)",
        "unit": "₽", "kind": "trip_total", "source": TRIP_SOURCE, "source_refs": refs, "synthetic": True, "role": "fit",
    })
    travel = option["travel"]
    long_trip = travel["duration_min"] > 300
    facts.append({
        "ref": f"trip:{city_id}:travel_min", "value": travel["duration_min"],
        "label": ("Перелёт" if travel["kind"] == "flight" else "Поезд") + " в одну сторону, минут",
        "unit": "мин", "kind": "transport_duration", "source": TRIP_SOURCE, "source_refs": refs[:1],
        "synthetic": True, "role": "risk" if long_trip else "fit",
    })
    city = cat.cities[city_id]
    visa_value = {"domestic": "виза не нужна", "visa_free": "безвизовый въезд",
                  "visa_on_arrival": "виза по прибытии"}.get(city.visa.regime, city.visa.regime)
    if city.visa.max_stay_days and city.visa.regime != "domestic":
        visa_value += f", до {city.visa.max_stay_days} дней"
    facts.append({
        "ref": f"city:{city_id}:visa", "value": visa_value, "label": f"Въезд для граждан РФ ({city.country})",
        "kind": "visa_rule", "source": VISA_SOURCE, "source_refs": [f"checked_at:{city.visa.checked_at}"],
        "synthetic": False, "role": "fit" if city.visa.regime == "domestic" else "risk",
    })
    return facts


def _alt_changes(change: Change, cat: Catalog) -> list[dict[str, Any]] | None:
    c = change.change
    if c["key"] == "budget_total_rub":
        out = [{"key": "budget_total_rub", "value": c["to"], "kind": "hard"}]
    elif c["key"] == "dates":
        out = [{"key": "dates", "value": c["to"], "kind": "hard"}]
    elif c["key"] == "destination":
        out = [{"key": "destination", "value": c.get("to_name") or cat.cities[c["to"]].name, "kind": "hard"}]
    elif c["key"] == "transport":
        out = [{"key": "transport", "value": "any", "kind": "hard"}]
    elif c["key"] == "max_stops":
        out = [{"key": "max_stops", "value": 1, "kind": "hard"}]
    else:
        return None
    if change.over_budget_rub and c["key"] != "budget_total_rub":
        # A destination/date change that still exceeds the budget only works together with the new ceiling.
        out.append({"key": "budget_total_rub", "value": change.total_rub, "kind": "hard"})
    if any(x["key"] not in B_CHANGE_KEYS for x in out):
        return None
    return out


def _alternative(change: Change, cat: Catalog, index: int) -> dict[str, Any] | None:
    changes = _alt_changes(change, cat)
    if changes is None or change.bundle is None:
        return None
    alt_id = f"alt-{change.kind}-{index}"
    ref = f"alt:{alt_id}:total_rub"
    bundle_refs = [f"offer:{o.offer_id}" for o in change.bundle.items]
    wire = change.to_wire()
    return {
        "id": alt_id,
        "label": change.text,
        "confirmed": True,
        "changes": changes,
        "refs": [ref],
        "evidence": [{
            "ref": ref, "value": change.total_rub, "label": "Стоимость поездки после изменения (собрана и проверена)",
            "unit": "₽", "kind": "trip_total", "source": TRIP_SOURCE, "source_refs": bundle_refs,
            "synthetic": True, "role": "fit",
        }],
        "kind": change.kind,
        "total_rub": change.total_rub,
        "over_budget_rub": change.over_budget_rub,
        "needs_consent": change.relaxes_hard,
        "bundle": wire["bundle"],
    }


def _alternatives(cat: Catalog, changes: list[Change]) -> list[dict[str, Any]]:
    out = []
    for i, change in enumerate(changes, start=1):
        alt = _alternative(change, cat, i)
        if alt:
            out.append(alt)
    return out[:2]


def _required(problems: list[str]) -> list[str]:
    out = []
    for p in problems:
        key = p.split(":", 1)[0]
        mapped = {"origin": "origin", "dates": "travel_window"}.get(key)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def _covered_names(cat: Catalog) -> str:
    return ", ".join(c.name for c in cat.cities.values() if "destination" in c.roles)


def suggest_destinations(requirements: dict[str, Any], state: Any = None) -> dict[str, Any]:
    cat = get_catalog()
    req, _ = merged_requirements(requirements, state)
    dest_key, dest_value = _pick(req, "destination", "dest", "destination_city", "to")
    explicit = resolve_city(cat, dest_value) if dest_key else None

    if dest_key and (explicit is None or "destination" not in cat.cities[explicit].roles):
        return {"destinations": [], "alternatives": [], "required_inputs": [],
                "notes": [f"Направления «{dest_value}» нет в каталоге. Доступны: {_covered_names(cat)}."]}

    if explicit:
        # Concrete trip: B already failed to build it and asks for confirmed minimal changes.
        q, problems = query_from_requirements(cat, requirements, state)
        if q is not None:
            changes = minimal_changes(cat, q)
            notes = [] if changes else ["Под эти условия поездка собирается — изменения не нужны."]
            return {"destinations": [], "alternatives": _alternatives(cat, changes), "notes": notes,
                    "required_inputs": []}

    dq, problems = destination_query_from_requirements(cat, requirements, state)
    if dq is None:
        return {"destinations": [], "alternatives": [], "notes": problems, "required_inputs": _required(problems)}
    result = suggest_destinations_for(cat, dq)
    destinations = []
    for option in result.options:
        option = dict(option)
        option["evidence"] = _destination_evidence(cat, option, dq.month)
        destinations.append(option)
    notes = [f"Предположение: {a}." for a in result.assumptions]
    notes += [f"{r['name']}: {r['reason']}." for r in result.rejected]
    alternatives: list[dict[str, Any]] = []
    if not destinations:
        best = closest_trip_query(cat, dq)
        if best is not None:
            alternatives = _alternatives(cat, minimal_changes(cat, best))
    return {"destinations": destinations, "alternatives": alternatives, "notes": notes, "required_inputs": []}

