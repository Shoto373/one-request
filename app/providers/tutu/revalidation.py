"""Read-only revalidation immediately before the local simulated checkout."""
from __future__ import annotations

import asyncio
from typing import Any

from app.providers.tutu.client import TutuMCPClient
from app.state.models import Check


def _same_transport(row: dict[str, Any], offer: dict[str, Any]) -> bool:
    if str(row.get("offer_id") or "") != str(offer.get("provider_offer_id") or ""):
        return False
    expected = offer.get("variant", {}).get("legs") or []
    actual = row.get("legs") or []
    def signature(legs):
        return tuple(
            (str(segment.get("voyage_no") or ""), str(segment.get("departure_at") or ""),
             str(segment.get("arrival_at") or ""))
            for leg in legs for segment in (leg.get("segments") or [])
        )
    return bool(expected) and signature(expected) == signature(actual)


async def validate_tutu_bundle(bundle: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Refresh each supplier item with a read-only search/details tool; never creates a cart at Tutu."""
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
                        expected_raw = str(terms.get("rate_price_amount") or terms.get("price_amount") or "")
                        for room in data.get("rooms") or []:
                            for rate in room.get("rates") or []:
                                price = rate.get("price") or {}
                                if price.get("currency") != "RUB":
                                    continue
                                identity_match = (bool(expected_hash) and rate.get("offerpack_hash") == expected_hash) or (
                                    not expected_hash and bool(expected_rate_id) and rate.get("id") == expected_rate_id
                                )
                                if identity_match:
                                    found = True
                                    same_price = str(price.get("amount")) == expected_raw
                                    actual_amount = price.get("amount")
                                    break
                            if found:
                                break
                else:
                    args = dict(ref.get("search_args") or offer.get("search_params") or {})
                    tool = "search_avia" if offer.get("kind") == "flight" else "search_rail"
                    expected_legs = offer.get("variant", {}).get("legs") or []
                    args["page"] = 1
                    # This is the observed, documented page size used by the search
                    # contract. If the exact result is outside this bounded page,
                    # fail closed instead of inventing a supplier-side filter.
                    args["page_size"] = 5
                    data = (await client.call(tool, args)).data
                    for row in data.get("offers") or []:
                        if not isinstance(row, dict) or not _same_transport(row, offer):
                            continue
                        found = True
                        current = (row.get("price") or {}) if offer.get("kind") == "flight" else (row.get("price_party") or {})
                        actual_amount = current.get("amount")
                        expected = offer.get("variant", {}).get("price_amount")
                        same_price = current.get("currency") == "RUB" and str(actual_amount) == str(expected)
                        break
                ref_id = f"offer:{offer.get('offer_id')}"
                title = offer.get("title") or supplier_id or "Предложение Туту"
                if not found:
                    checks.append(Check(kind="availability", status="block", refs=[ref_id],
                                        text=f"{title}: при повторной проверке Туту не вернуло то же предложение.",
                                        options=["Пересобрать актуальный комплект"]).model_dump(mode="json"))
                elif not same_price:
                    checks.append(Check(kind="availability", status="block", refs=[ref_id],
                                        text=f"{title}: цена изменилась при повторной проверке ({actual_amount} RUB); нужно пересобрать план и подтвердить новую цену.",
                                        options=["Пересобрать план и проверить разницу"]).model_dump(mode="json"))
                else:
                    checks.append(Check(kind="availability", status="ok", refs=[ref_id],
                                        text=f"{title}: то же предложение и цена повторно найдены у Туту перед имитацией оформления.").model_dump(mode="json"))
    except Exception as exc:
        # Fail closed: a network or protocol error never means that the old result is current.
        for offer in offers:
            checks.append(Check(kind="availability", status="block", refs=[f"offer:{offer.get('offer_id')}"],
                                text=f"{offer.get('title') or 'Предложение Туту'}: не удалось повторно проверить актуальность у Туту ({type(exc).__name__}).",
                                options=["Повторить проверку или выполнить новый поиск"]).model_dump(mode="json"))
    return checks


def validate_tutu_bundle_sync(bundle: dict[str, Any], state: Any = None) -> list[dict[str, Any]]:
    """Sync bridge used by the existing synchronous cart routes in their worker thread."""
    return asyncio.run(validate_tutu_bundle(bundle, state))
