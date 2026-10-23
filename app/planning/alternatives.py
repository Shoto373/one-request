"""Minimal changes for an infeasible trip (sprint 2, A3).

When ``build_trip_bundles`` finds nothing, try relaxing one thing at a time: dates ±1–3 days,
a shorter stay, transport limits, another destination, the budget. Every variant is built for
real (with the budget ignored) and costs ``over_budget_rub`` plus a change magnitude.
The two best changes of different kinds are returned with their price.

Nothing here is applied automatically: a change that touches a hard requirement is
marked ``needs_consent`` and ranked after the ones that do not.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from app.catalog.store import Catalog
from app.planning.build import build_trip_bundles, limits_text
from app.planning.models import Bundle, bundle_to_wire
from app.planning.query import TripQuery

DATE_SHIFTS = (1, -1, 2, -2, 3, -3)
MAX_SHORTER_NIGHTS = 3


def rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def plural_days(n: int) -> str:
    n = abs(n)
    word = "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней"
    return f"{n} {word}"


@dataclass
class Change:
    kind: str  # dates | nights | transport | destination | budget
    text: str
    total_rub: int
    over_budget_rub: int
    relaxes_hard: bool
    change: dict[str, Any]
    magnitude: float
    bundle: Bundle | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "total_rub": self.total_rub,
            "over_budget_rub": self.over_budget_rub,
            "needs_consent": self.relaxes_hard,
            "change": self.change,
            "bundle": bundle_to_wire(self.bundle) if self.bundle else None,
        }


def _cheapest(cat: Catalog, q: TripQuery) -> Bundle | None:
    """Cheapest feasible bundle with the budget ignored (budget is accounted separately)."""
    res = build_trip_bundles(cat, replace(q, budget_rub=None, budget_hard=False))
    return res.bundles[0] if res.bundles else None


def _candidate(q: TripQuery, kind: str, text: str, bundle: Bundle, change: dict, magnitude: float,
               relaxes: bool) -> Change:
    over = max(0, bundle.total_rub - q.budget_rub) if q.budget_rub is not None else 0
    full_text = text + (f" — {rub(bundle.total_rub)}" if kind != "budget" else "")
    if over and kind != "budget":
        full_text += f" (+{rub(over)} к бюджету)"
    return Change(kind=kind, text=full_text, total_rub=bundle.total_rub, over_budget_rub=over,
                  relaxes_hard=relaxes or (over > 0 and q.budget_hard), change=change, magnitude=magnitude,
                  bundle=bundle)


def candidate_changes(cat: Catalog, q: TripQuery) -> list[Change]:
    out: list[Change] = []
    nights = (q.return_date - q.depart_date).days

    base = _cheapest(cat, q)
    if base is not None and q.budget_rub is not None and base.total_rub > q.budget_rub:
        diff = base.total_rub - q.budget_rub
        out.append(_candidate(q, "budget", f"Увеличить бюджет на {rub(diff)} — до {rub(base.total_rub)}", base,
                              {"key": "budget_total_rub", "from": q.budget_rub, "to": base.total_rub},
                              magnitude=0.0, relaxes=q.budget_hard))

    for shift in DATE_SHIFTS:
        q2 = replace(q, depart_date=q.depart_date + timedelta(days=shift), return_date=q.return_date + timedelta(days=shift))
        b = _cheapest(cat, q2)
        if b is not None:
            where = "позже" if shift > 0 else "раньше"
            out.append(_candidate(
                q, "dates", f"Даты на {plural_days(shift)} {where}: {q2.depart_date:%d.%m}–{q2.return_date:%d.%m}", b,
                {"key": "dates", "from": {"start": str(q.depart_date), "end": str(q.return_date)},
                 "to": {"start": str(q2.depart_date), "end": str(q2.return_date)}},
                magnitude=abs(shift), relaxes=q.dates_hard and abs(shift) > q.date_flex_days))
            break

    for fewer in range(1, min(MAX_SHORTER_NIGHTS, nights - 1) + 1):
        q2 = replace(q, return_date=q.return_date - timedelta(days=fewer))
        b = _cheapest(cat, q2)
        if b is not None and (base is None or b.total_rub < base.total_rub):
            out.append(_candidate(
                q, "nights", f"На {plural_days(fewer)} короче: {q2.depart_date:%d.%m}–{q2.return_date:%d.%m}", b,
                {"key": "dates", "from": {"start": str(q.depart_date), "end": str(q.return_date)},
                 "to": {"start": str(q2.depart_date), "end": str(q2.return_date)}},
                magnitude=1.5 * fewer, relaxes=q.dates_hard))
            break

    if q.limits_hard and (q.max_stops is not None or q.max_travel_min is not None):
        b = _cheapest(cat, replace(q, max_stops=None, max_travel_min=None, limits_hard=False))
        if b is not None:
            out.append(_candidate(q, "transport", f"Снять ограничение «{limits_text(q)}»", b,
                                  {"key": "max_stops", "from": q.max_stops, "to": None}, magnitude=1.0, relaxes=True))
    if q.kinds != ("flight", "train"):
        b = _cheapest(cat, replace(q, kinds=("flight", "train")))
        if b is not None:
            other = "самолёт" if q.kinds == ("train",) else "поезд"
            out.append(_candidate(q, "transport", f"Разрешить {other}", b,
                                  {"key": "transport", "from": list(q.kinds), "to": ["flight", "train"]},
                                  magnitude=1.0, relaxes=False))

    best_dest: Change | None = None
    for city in sorted(cat.cities.values(), key=lambda c: c.id):
        if "destination" not in city.roles or city.id == q.dest_city:
            continue
        b = _cheapest(cat, replace(q, dest_city=city.id))
        if b is None:
            continue
        c = _candidate(q, "destination", f"Другое направление — {city.name}", b,
                       {"key": "destination", "from": q.dest_city, "to": city.id, "to_name": city.name},
                       magnitude=4.0, relaxes=q.dest_hard)
        if best_dest is None or (c.over_budget_rub, c.total_rub) < (best_dest.over_budget_rub, best_dest.total_rub):
            best_dest = c
    if best_dest:
        out.append(best_dest)
    return out


def is_satisfied(cat: Catalog, q: TripQuery) -> bool:
    """A feasible bundle exists and fits the budget (a soft budget only warns in the bundle)."""
    res = build_trip_bundles(cat, q)
    if not res.bundles:
        return False
    return q.budget_rub is None or res.bundles[0].total_rub <= q.budget_rub


def minimal_changes(cat: Catalog, q: TripQuery, limit: int = 2) -> list[Change]:
    """Up to ``limit`` cheapest one-step changes of different kinds; [] if the trip already fits."""
    if is_satisfied(cat, q):
        return []
    candidates = candidate_changes(cat, q)
    budget_only = next((c for c in candidates if c.kind == "budget"), None)
    if budget_only is not None:
        # A date/length change that still needs most of the extra money is just a worse "raise the budget".
        candidates = [c for c in candidates if not (c.kind in ("dates", "nights") and c.over_budget_rub
                                                    and c.over_budget_rub > 0.7 * budget_only.over_budget_rub)]
    ranked = sorted(candidates, key=lambda c: (c.relaxes_hard, c.over_budget_rub, c.magnitude, c.total_rub))
    out, kinds = [], set()
    for c in ranked:
        if c.kind in kinds:
            continue
        kinds.add(c.kind)
        out.append(c)
        if len(out) == limit:
            break
    return out


def trip_alternatives(requirements: dict[str, Any], state: Any = None, limit: int = 2) -> list[dict[str, Any]]:
    """Orchestrator contract: two minimal changes for a concrete trip that has no fitting bundle."""
    from app.catalog.store import get_catalog
    from app.planning.query import query_from_requirements

    cat = get_catalog()
    q, _problems = query_from_requirements(cat, requirements, state)
    if q is None:
        return []
    return [c.to_wire() for c in minimal_changes(cat, q, limit)]
