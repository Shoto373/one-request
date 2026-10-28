"""Read-only revalidation immediately before the local simulated checkout."""
from __future__ import annotations

import asyncio
from typing import Any

from app.providers.tutu.client import TutuMCPClient
from app.state.models import Check


def _signature(legs: list[dict[str, Any]]) -> tuple:
    return tuple((str(segment.get("voyage_no") or ""), str(segment.get("departure_at") or "")[:16])
                 for leg in legs for segment in (leg.get("segments") or []))


def _same_transport(row: dict[str, Any], offer: dict[str, Any]) -> bool:
    """Same trip = same flight/train numbers at the same departure times. Tutu issues a new
    offer id on every search, so the supplier id is not a stable identity."""
    expected = offer.get("variant", {}).get("legs") or []
    return bool(expected) and _signature(expected) == _signature(row.get("legs") or [])


def _rub(amount: Any) -> str:
    try:
        return f"{float(amount):,.0f} ₽".replace(",", " ")
    except (TypeError, ValueError):
        return "новая цена"


def _same_amount(actual: Any, expected: Any) -> bool:
    try:
        return abs(float(actual) - float(expected)) < 1
    except (TypeError, ValueError):
        return False


async def validate_tutu_bundle(bundle: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Refresh each supplier item with a read-only search/details tool; never creates a cart at Tutu.
    A dropped MCP session is retried once before the check fails closed."""
    checks = await _validate_once(bundle)
    if any("не удалось проверить актуальность" in c.get("text", "") for c in checks):
        checks = await _validate_once(bundle)
    return checks


async def _validate_once(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    offers = [o for o in bundle.get("items", []) if o.get("provider") == "tutu"]
    if not offers:
        return list(bundle.get("checks") or [])
    checks = list(bundle.get("checks") or [])
    try:
        async with TutuMCPClient() as client:
            for offer in offers:
                ref = offer.get("revalidation") or {}
                supplier_id = str(offer.get("provider_offer_id") or "")
                found = False
                same_price = False
                actual_amount = None
                if offer.get("kind") == "hotel":
                    args = ref.get("args") or offer.get("variant", {}).get("terms", {}).get("details_ref")
                    if isinstance(args, dict):
                        data = (await client.call("get_offer_details", args)).data
                        terms = offer.get("variant", {}).get("terms", {})
                        expected_hash = terms.get("offerpack_hash")
                        expected_rate_id = terms.get("rate_id")
                        expected_raw = terms.get("rate_price_amount") or terms.get("price_amount")
                        expected_room = str(terms.get("room_name") or "").strip().lower()
                        same_room: list[Any] = []
                        for room in data.get("rooms") or []:
                            for rate in room.get("rates") or []:
                                price = rate.get("price") or {}
                                if price.get("currency") != "RUB":
                                    continue
                                identity_match = (bool(expected_hash) and rate.get("offerpack_hash") == expected_hash) or (
                                    bool(expected_rate_id) and rate.get("id") == expected_rate_id
                                )
                                if identity_match:
                                    found = True
                                    same_price = _same_amount(price.get("amount"), expected_raw)
                                    actual_amount = price.get("amount")
                                    break
                                if expected_room and str(room.get("room_name") or "").strip().lower() == expected_room:
                                    same_room.append(price.get("amount"))
                            if found:
                                break
                        if not found and same_room:
                            # Rate ids rotate between searches: the same room counts as the same offer.
                            found = True
                            match = next((a for a in same_room if _same_amount(a, expected_raw)), None)
                            same_price = match is not None
                            actual_amount = match if match is not None else min(same_room, key=lambda a: float(a or 0))
                else:
                    args = dict(ref.get("search_args") or offer.get("search_params") or {})
                    tool = "search_avia" if offer.get("kind") == "flight" else "search_rail"
                    expected_legs = offer.get("variant", {}).get("legs") or []
                    args["page"] = 1
                    args["page_size"] = 10
                    numbers = sorted({str(seg.get("voyage_no")) for leg in expected_legs
                                      for seg in (leg.get("segments") or []) if seg.get("voyage_no")})
                    if tool == "search_avia" and numbers:
                        args["flight_numbers"] = numbers  # server-side filter: «возьми рейс N»
                    data = (await client.call(tool, args)).data
                    for row in data.get("offers") or []:
                        if not isinstance(row, dict) or not _same_transport(row, offer):
                            continue
                        found = True
                        current = (row.get("price") or {}) if offer.get("kind") == "flight" else (row.get("price_party") or {})
                        actual_amount = current.get("amount")
                        expected = offer.get("variant", {}).get("price_amount")
                        same_price = current.get("currency") == "RUB" and _same_amount(actual_amount, expected)
                        break
                ref_id = f"offer:{offer.get('offer_id')}"
                title = offer.get("title") or supplier_id or "Предложение"
                if not found:
                    checks.append(Check(kind="availability", status="block", refs=[ref_id],
                                        text=f"{title}: этого предложения больше нет в продаже — пересоберите план.",
                                        options=["Пересобрать актуальный комплект"]).model_dump(mode="json"))
                elif not same_price:
                    checks.append(Check(kind="availability", status="block", refs=[ref_id],
                                        text=f"{title}: цена изменилась, сейчас {_rub(actual_amount)} — пересоберите план, чтобы подтвердить новую цену.",
                                        options=["Пересобрать план и проверить разницу"]).model_dump(mode="json"))
                else:
                    checks.append(Check(kind="availability", status="ok", refs=[ref_id],
                                        text=f"{title}: перед оформлением ещё раз проверили — в продаже по той же цене.").model_dump(mode="json"))
    except Exception as exc:
        # Fail closed: a network or protocol error never means that the old result is current.
        for offer in offers:
            checks.append(Check(kind="availability", status="block", refs=[f"offer:{offer.get('offer_id')}"],
                                text=f"{offer.get('title') or 'Предложение'}: не удалось проверить актуальность перед оформлением — повторите через минуту.",
                                options=["Повторить проверку или выполнить новый поиск"]).model_dump(mode="json"))
    return checks


def validate_tutu_bundle_sync(bundle: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Sync bridge used by the existing synchronous cart routes in their worker thread."""
    return asyncio.run(validate_tutu_bundle(bundle, state))
