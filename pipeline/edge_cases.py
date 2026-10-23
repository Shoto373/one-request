"""Edge cases planted into the catalog on purpose (sprint 1, A3) and their registry.

Every planted case is listed in ``meta.json`` -> ``edge_cases`` with refs, so the agent
team and eval scenarios can target them by id.
"""

from __future__ import annotations

from datetime import date, datetime

from pipeline import config
from pipeline.util import rng

CLOSED_SEASON = [
    {"start": "2026-04-15", "end": "2026-11-01", "note": "летний сезон"},
    {"start": "2027-04-15", "end": "2027-11-01", "note": "летний сезон"},
]
EVN_SOLD_OUT = {"start": "2026-11-20", "end": "2026-11-24", "note": "город заполнен (синтетический сценарий)"}


def _cheapest(hotels: list[dict]) -> dict:
    return min(hotels, key=lambda h: (h["rooms"][0]["base_price_rub"], h["id"]))


def plant_hotels(hotels: list[dict]) -> list[dict]:
    """Mutates hotels in place; returns EdgeCase dicts."""
    by_city: dict[str, list[dict]] = {}
    for h in hotels:
        by_city.setdefault(h["city_id"], []).append(h)
    edges: list[dict] = []

    # Antalya: two priciest beach resorts work only in summer -> November has fewer options.
    ayt = sorted(by_city["AYT"], key=lambda h: (h["kind"] != "resort", -h["rooms"][0]["base_price_rub"], h["id"]))
    closed = ayt[:2]
    for h in closed:
        h["open_periods"] = [dict(p) for p in CLOSED_SEASON]
    edges.append({"id": "ec_ayt_closed_season", "kind": "hotel_closed_season", "refs": [h["id"] for h in closed],
                  "note": "Отели Антальи закрыты с 1 ноября до 15 апреля: поиск на ноябрь их не возвращает."})

    # No 24h reception and no late arrival: cheapest open hotel in Sochi and Antalya.
    for cid, opens, closes in (("AER", "08:00", "22:00"), ("AYT", "08:00", "22:00")):
        pool = [h for h in by_city[cid] if not h["open_periods"]]
        h = _cheapest(pool)
        h["reception"] = {"hours": {"round_the_clock": False, "opens": opens, "closes": closes},
                          "late_arrival_possible": False,
                          "note": f"Стойка работает {opens}–{closes}, заселение ночью невозможно."}
        edges.append({"id": f"ec_{cid.lower()}_no_night_reception", "kind": "no_24h_reception", "refs": [h["id"]],
                      "note": f"Самый дешёвый отель ({h['name']}) без круглосуточной стойки: ночной прилёт -> block."})

    # Reception closes, but a keybox allows late arrival -> warn, not block.
    h = _cheapest([x for x in by_city["KGD"]])
    h["reception"] = {"hours": {"round_the_clock": False, "opens": "09:00", "closes": "21:00"},
                      "late_arrival_possible": True,
                      "note": "После 21:00 — заселение через ключницу по коду из письма."}
    edges.append({"id": "ec_kgd_keybox", "kind": "no_24h_reception", "refs": [h["id"]],
                  "note": "Стойка 09:00–21:00, поздний заезд через ключницу: ночной прилёт -> warn."})

    # Pets: every destination keeps both pet-friendly and no-pets hotels.
    no_pets_refs = []
    for cid, items in sorted(by_city.items()):
        need_yes = 1 if cid in config.NO_PET_TRANSFER_CITIES else 2
        items = sorted(items, key=lambda h: h["id"])
        yes = [h for h in items if h["pets"]["allowed"]]
        for h in [x for x in items if not x["pets"]["allowed"]]:
            if len(yes) >= need_yes:
                break
            h["pets"] = {"allowed": True, "max_weight_kg": 10, "fee_rub_per_night": 800, "note": None}
            yes.append(h)
        no = [h for h in items if not h["pets"]["allowed"]]
        for h in reversed(yes):
            if len(no) >= 2:
                break
            if len([x for x in items if x["pets"]["allowed"]]) <= need_yes:
                break
            h["pets"] = {"allowed": False, "max_weight_kg": None, "fee_rub_per_night": 0, "note": None}
            no.append(h)
        no_pets_refs += [h["id"] for h in items if not h["pets"]["allowed"]]
    edges.append({"id": "ec_no_pets_hotels", "kind": "no_pets_hotel", "refs": no_pets_refs,
                  "note": "Отели без животных: при собаке они выпадают из поиска."})

    # Whole destination without rooms for some dates.
    for h in by_city["EVN"]:
        h["sold_out"].append(dict(EVN_SOLD_OUT))
    edges.append({"id": "ec_evn_sold_out", "kind": "no_options_dates", "refs": ["city:EVN", "2026-11-20/2026-11-24"],
                  "note": "В Ереване нет свободных номеров в ночи 20–23 ноября 2026."})

    # A few random short sold-out ranges elsewhere.
    for h in hotels:
        r = rng("soldout", h["id"])
        if h["city_id"] != "EVN" and r.random() < 0.15:
            start = date(2026, 11, r.randint(1, 25))
            h["sold_out"].append({"start": start.isoformat(),
                                  "end": date(2026, 11, start.day + r.randint(2, 4)).isoformat(), "note": None})
    return edges


def _first_example(items: list[dict], pred, on_or_after: str = "2026-11-10") -> dict | None:
    cands = [x for x in items if x["depart"][:10] >= on_or_after and pred(x)]
    return min(cands, key=lambda x: x["depart"]) if cands else None


def scan_transport(flights: list[dict], trains: list[dict]) -> list[dict]:
    edges = []

    def night_next_day(x: dict) -> bool:
        dep, arr = datetime.fromisoformat(x["depart"]), datetime.fromisoformat(x["arrive"])
        return arr.date() > dep.date() and arr.hour < 6

    refs = []
    for a, b in (("SVX", "AYT"), ("SVO", "AER"), ("DME", "KGD"), ("DME", "DXB")):
        ex = _first_example(flights, lambda f, a=a, b=b: f["from_hub"] == a and f["to_hub"] == b and night_next_day(f))
        if ex:
            refs.append(ex["id"])
    tr = _first_example(trains, night_next_day)
    if tr:
        refs.append(tr["id"])
    edges.append({"id": "ec_night_arrival", "kind": "night_arrival_next_day", "refs": refs,
                  "note": "Вылет вечером, прилёт ночью следующего дня по местному времени: заезд сдвигается."})

    edges.append({"id": "ec_svx_ayt_program", "kind": "out_of_schedule",
                  "refs": ["route:SVX-AYT:U6", "2026-11-16"],
                  "note": "Ежедневная программа Екатеринбург–Анталья до 15.11, дальше только ср и вс."})
    edges.append({"id": "ec_window_end", "kind": "out_of_schedule",
                  "refs": [f"window:{config.WINDOW_START}/{config.WINDOW_END}"],
                  "note": "Транспорт есть только в окне каталога; даты вне окна -> нет вариантов."})
    edges.append({"id": "ec_ssh_svx_days", "kind": "no_options_dates", "refs": ["route:SVX-SSH:U6"],
                  "note": "Из Екатеринбурга в Шарм-эль-Шейх прямые только вт и сб; в другие дни — с пересадкой."})

    dxb = sorted({f"route:{f['from_hub']}-{f['to_hub']}" for f in flights if "DXB" in (f["from_hub"], f["to_hub"])})
    edges.append({"id": "ec_dxb_pets", "kind": "no_pets_transport", "refs": dxb,
                  "note": "В ОАЭ животное пассажиром не провезти: Дубай с собакой невыполним."})
    edges.append({"id": "ec_n4_pets", "kind": "no_pets_transport", "refs": ["carrier:N4"],
                  "note": "Чартер Nordwind не берёт животных."})
    edges.append({"id": "ec_trains", "kind": "train_route",
                  "refs": sorted({f"train:{t['train_no']}:{t['from_hub']}-{t['to_hub']}" for t in trains}),
                  "note": "ЖД: Москва↔Казань, Москва↔Сочи/Адлер, СПб↔Москва, Екатеринбург↔Казань."})
    return edges


def transfer_edges() -> list[dict]:
    return [
        {"id": "ec_no_pets_transfer", "kind": "no_pets_transfer",
         "refs": [f"city:{c}" for c in sorted(config.NO_PET_TRANSFER_CITIES)],
         "note": "Ни один трансфер в Шарм-эль-Шейхе и Дубае не берёт животных; в других городах — не каждое такси."},
        {"id": "ec_night_public", "kind": "no_options_dates", "refs": ["mode:public", "mode:shuttle"],
         "note": "Ночью автобус и шаттл не ходят: после 23:00 остаётся только такси/минивэн."},
        {"id": "ec_budget_dxb", "kind": "budget_infeasible", "refs": ["city:DXB"],
         "note": "«Дубай на неделю на троих за 30 000» — невыполним: только перелёт дороже бюджета."},
    ]
