from types import SimpleNamespace

import pytest

from app.providers.tutu import revalidation


class FakeMCP:
    response = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def call(self, tool, args):
        assert tool == "get_offer_details"
        assert args["hotel_id"] == "hotel-7"
        return SimpleNamespace(data=self.response)


@pytest.mark.asyncio
async def test_hotel_revalidation_requires_the_exact_rate_not_only_the_same_price(monkeypatch):
    FakeMCP.response = {"rooms": [{"rates": [{"offerpack_hash": "different-rate",
                                                  "price": {"amount": 20816, "currency": "RUB"}}]}]}
    monkeypatch.setattr(revalidation, "TutuMCPClient", FakeMCP)
    bundle = {"items": [{"offer_id": "offer-1", "provider": "tutu", "provider_offer_id": "selected-rate",
                         "kind": "hotel", "title": "Hotel", "revalidation": {"args": {"hotel_id": "hotel-7"}},
                         "variant": {"terms": {"offerpack_hash": "selected-rate", "rate_price_amount": 20816}}}],
              "checks": []}
    checks = await revalidation.validate_tutu_bundle(bundle)
    assert checks[-1]["status"] == "block"
    assert "не вернуло то же предложение" in checks[-1]["text"]


@pytest.mark.asyncio
async def test_hotel_revalidation_accepts_same_hash_and_price(monkeypatch):
    FakeMCP.response = {"rooms": [{"rates": [{"offerpack_hash": "selected-rate",
                                                  "price": {"amount": 20816, "currency": "RUB"}}]}]}
    monkeypatch.setattr(revalidation, "TutuMCPClient", FakeMCP)
    bundle = {"items": [{"offer_id": "offer-1", "provider": "tutu", "provider_offer_id": "selected-rate",
                         "kind": "hotel", "title": "Hotel", "revalidation": {"args": {"hotel_id": "hotel-7"}},
                         "variant": {"terms": {"offerpack_hash": "selected-rate", "rate_price_amount": 20816}}}],
              "checks": []}
    checks = await revalidation.validate_tutu_bundle(bundle)
    assert checks[-1]["status"] == "ok"
