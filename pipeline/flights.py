"""Synthetic dated flights over real airport geometry.

Block time = great-circle distance / 750 km/h + 40 min. Schedules come from
``config.ROUTES`` / ``config.CONNECTIONS``; return legs are mirrored with a turnaround.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pipeline import config
from pipeline.util import days, demand_factor, haversine_km, local_dt, rng, round_minutes, round_to, ymd

GEN_REF = f"gen:flights@seed{config.RANDOM_SEED}"
FORMULA_REF = "formula:block_time=distance/750kmh+40min"


class FlightBuilder:
    def __init__(self, hubs: list[dict], cities: list[dict]):
        self.hub = {h["id"]: h for h in hubs}
        self.country = {c["id"]: c["country_code"] for c in cities}
        self.used_numbers: dict[str, set[int]] = defaultdict(set)
        self.numbers: dict[tuple, int] = {}
        self.flights: list[dict] = []

    # ------------------------------------------------------------------ helpers

    def _number(self, carrier: str, *key: object) -> int:
        """Even outbound number; the return leg gets number + 1. Same key -> same number."""
        cache_key = (carrier, *key)
        if cache_key in self.numbers:
            return self.numbers[cache_key]
        r = rng("flno", carrier, *key)
        n = r.randrange(50, 2400) * 2
        while n in self.used_numbers[carrier] or n + 1 in self.used_numbers[carrier]:
            n += 2
        self.used_numbers[carrier].update({n, n + 1})
        self.numbers[cache_key] = n
        return n

    def _dist(self, a: str, b: str) -> float:
        ha, hb = self.hub[a]["coord"], self.hub[b]["coord"]
        return haversine_km(ha["lat"], ha["lon"], hb["lat"], hb["lon"])

    def _block(self, a: str, b: str) -> int:
        return round_minutes(self._dist(a, b) / 750 * 60 + 40)

    def _intl(self, a: str, b: str) -> bool:
        ca, cb = self.hub[a]["city_id"], self.hub[b]["city_id"]
        return self.country[ca] != self.country[cb]

    def _aircraft(self, carrier: str, dist: float) -> str:
        if carrier == "ZF":
            return "Boeing 757-200"
        if carrier == "EK":
            return "Boeing 777-300ER"
        if carrier in ("SU", "FV") and dist < 1500:
            return "Sukhoi Superjet 100"
        return "Airbus A321neo" if dist > 2500 else "Airbus A320neo"

    def _segment(self, carrier: str, num: int, a: str, b: str, dep: datetime, minutes: int) -> dict:
        arr = (dep + timedelta(minutes=minutes)).astimezone(ZoneInfo(self.hub[b]["timezone"]))
        return {
            "flight_no": f"{carrier} {num}",
            "carrier": carrier,
            "from_hub": a,
            "to_hub": b,
            "depart": dep.isoformat(),
            "arrive": arr.isoformat(),
            "duration_min": minutes,
            "aircraft": self._aircraft(carrier, self._dist(a, b)),
        }

    def _pets(self, carrier: str, a: str, b: str) -> dict:
        pets = dict(config.CARRIERS[carrier]["pets"])
        pets.setdefault("note", None)
        for hub in (a, b):
            cc = self.country[self.hub[hub]["city_id"]]
            if cc in config.PET_BLOCKED_COUNTRIES:
                return {"cabin": False, "cabin_max_kg": None, "cabin_fee_rub": 0, "hold": False, "hold_fee_rub": 0,
                        "note": config.PET_BLOCKED_COUNTRIES[cc]}
        return pets

    def _fares(self, carrier: str, dist: float, intl: bool, dep: datetime, key: str, discount: float = 1.0) -> list[dict]:
        r = rng("fare", key)
        rate = 6.2 if intl else 4.6
        base = (2200 + dist * rate) * config.CARRIERS[carrier]["price"] * demand_factor(dep.date())
        base *= r.uniform(0.85, 1.22) * discount
        if dep.hour < 6 or dep.hour >= 22:
            base *= 0.9
        cabin = 10 if carrier != "EK" else 7
        out = []
        for code in config.CARRIERS[carrier]["fares"]:
            if code == "light":
                out.append({"code": "light", "name": "Лайт", "price_rub": round_to(base, 10),
                            "baggage": {"cabin_kg": cabin, "checked_pieces": 0, "checked_kg": None},
                            "refundable": False, "refund_fee_rub": None,
                            "seats_left": r.choice([0, 0, 3, 5, 8, 12, 18, 25])})
            elif code == "standard":
                kg = 20 if carrier in ("ZF", "N4") else 23
                out.append({"code": "standard", "name": "Стандарт" if carrier not in ("ZF", "N4") else "Чартер",
                            "price_rub": round_to(base * 1.18 + 900, 10),
                            "baggage": {"cabin_kg": cabin, "checked_pieces": 1, "checked_kg": kg},
                            "refundable": False, "refund_fee_rub": None,
                            "seats_left": r.randint(1, 30)})
            else:
                pieces = 2 if carrier in ("EK", "TK") else 1
                out.append({"code": "flex", "name": "Гибкий", "price_rub": round_to(base * 1.75 + 1500, 10),
                            "baggage": {"cabin_kg": cabin, "checked_pieces": pieces, "checked_kg": 23},
                            "refundable": True, "refund_fee_rub": round_to(base * 0.1, 100),
                            "seats_left": r.randint(1, 9)})
        return out

    def _add(self, carrier: str, segs: list[dict]) -> None:
        first, last = segs[0], segs[-1]
        dep = datetime.fromisoformat(first["depart"])
        arr = datetime.fromisoformat(last["arrive"])
        a, b = first["from_hub"], last["to_hub"]
        nums = [s["flight_no"].split()[1] for s in segs]
        fid = f"fl_{carrier}{'-'.join(nums)}_{ymd(dep.date())}"
        dist = sum(self._dist(s["from_hub"], s["to_hub"]) for s in segs)
        self.flights.append(
            {
                "id": fid,
                "carrier": carrier,
                "carrier_name": config.CARRIERS[carrier]["name"],
                "flight_no": " / ".join(s["flight_no"] for s in segs),
                "origin_city": self.hub[a]["city_id"],
                "dest_city": self.hub[b]["city_id"],
                "from_hub": a,
                "to_hub": b,
                "depart": first["depart"],
                "arrive": last["arrive"],
                "duration_min": int((arr - dep).total_seconds() // 60),
                "stops": len(segs) - 1,
                "segments": segs,
                "fares": self._fares(carrier, dist, self._intl(a, b), dep, fid, 0.82 if len(segs) > 1 else 1.0),
                "pets": self._pets(carrier, a, b),
                "international": self._intl(a, b),
                "infant_free_age": 2,
                "synthetic": True,
                "source_refs": [GEN_REF, FORMULA_REF],
            }
        )

    # ------------------------------------------------------------------ schedules

    def _op_days(self, weekdays, op) -> list[date]:
        start, end = (date.fromisoformat(op[0]), date.fromisoformat(op[1])) if op else (config.WINDOW_START, config.WINDOW_END)
        return [d for d in days(start, end) if weekdays is None or d.weekday() in weekdays]

    def direct(self, carrier: str, a: str, b: str, times: list[str], weekdays, op) -> None:
        out_min = self._block(a, b)
        for t in times:
            key = (a, b, t)
            num = self._number(carrier, *key)
            r = rng("route", carrier, *key)
            back_min = out_min + r.choice([-10, -5, 0, 5, 10])
            turnaround = r.choice([70, 80, 95, 110])
            for day in self._op_days(weekdays, op):
                dep = local_dt(day, t, self.hub[a]["timezone"])
                seg = self._segment(carrier, num, a, b, dep, out_min)
                self._add(carrier, [seg])
                dep_back = datetime.fromisoformat(seg["arrive"]) + timedelta(minutes=turnaround)
                if dep_back.date() < config.WINDOW_END:
                    self._add(carrier, [self._segment(carrier, num + 1, b, a, dep_back, back_min)])

    def connection(self, carrier: str, a: str, via: str, b: str, t: str, weekdays) -> None:
        n1 = self._number(carrier, a, via, b, t, 1)
        n2 = self._number(carrier, a, via, b, t, 2)
        r = rng("conn", carrier, a, via, b, t)
        lay_out, lay_back = r.choice([85, 110, 135, 160]), r.choice([90, 115, 140, 175])
        m1, m2 = self._block(a, via), self._block(via, b)
        for day in self._op_days(weekdays, None):
            s1 = self._segment(carrier, n1, a, via, local_dt(day, t, self.hub[a]["timezone"]), m1)
            s2 = self._segment(carrier, n2, via, b, datetime.fromisoformat(s1["arrive"]) + timedelta(minutes=lay_out), m2)
            self._add(carrier, [s1, s2])
            b1 = self._segment(carrier, n2 + 1, b, via, datetime.fromisoformat(s2["arrive"]) + timedelta(minutes=90), m2)
            b2 = self._segment(carrier, n1 + 1, via, a, datetime.fromisoformat(b1["arrive"]) + timedelta(minutes=lay_back), m1)
            if datetime.fromisoformat(b1["depart"]).date() < config.WINDOW_END:
                self._add(carrier, [b1, b2])


def build_flights(hubs: list[dict], cities: list[dict]) -> list[dict]:
    fb = FlightBuilder(hubs, cities)
    for carrier, a, b, times, weekdays, op in config.ROUTES:
        fb.direct(carrier, a, b, times, weekdays, op)
    for carrier, a, via, b, t, weekdays in config.CONNECTIONS:
        fb.connection(carrier, a, via, b, t, weekdays)
    ids = [f["id"] for f in fb.flights]
    assert len(ids) == len(set(ids)), "duplicate flight ids"
    return sorted(fb.flights, key=lambda f: (f["depart"][:10], f["id"]))
