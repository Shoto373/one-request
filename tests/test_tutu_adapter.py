from app.providers.tutu.adapter import hotel_offer, transport_offer


def test_rail_normalizes_full_party_total_and_preserves_station_segments():
    row = {
        "offer_id": "rail-1", "price_party": {"amount": "14384.64", "currency": "RUB"},
        "departure_at": "2026-11-22T19:50:00+03:00", "arrival_at": "2026-11-24T04:35:00+03:00",
        "legs": [{"segments": [{"voyage_no": "304M", "departure_at": "2026-11-22T19:50:00+03:00",
                                 "arrival_at": "2026-11-24T04:35:00+03:00",
                                 "from_station": {"name": "Казанский вокзал"},
                                 "to_station": {"name": "Сочи"}}]}],
        "details_ref": {"id": "opaque"}, "variants": [{"conditions": {"baggage": None}}],
    }
    offer = transport_offer(row, kind="train", direction="outbound",
                            search_args={"origin": "Москва", "destination": "Сочи"},
                            fetched_at="2026-10-25T12:00:00+00:00")
    assert offer is not None
    assert offer.total_rub == 14385  # rounded only for the legacy integer-RUB model
    assert offer.variant["price_amount"] == "14384.64"
    assert offer.variant["price_basis"] == "party_estimate_same_fare"
    assert offer.variant["legs"][0]["segments"][0]["from_station"]["name"] == "Казанский вокзал"
    assert offer.data_mode == "estimated"
    assert offer.fetched_at is not None


def test_transport_offer_keeps_resolved_route_places_as_first_class_fields():
    row = {"offer_id": "rail-kzn-sochi", "price_party": {"amount": 8000, "currency": "RUB"},
           "departure_at": "2026-11-22T19:50:00+03:00", "arrival_at": "2026-11-24T04:35:00+03:00"}
    offer = transport_offer(row, kind="train", direction="outbound",
                            search_args={"origin": "Казань", "destination": "Сочи"},
                            resolved_places={"from": {"geo_id": "2656873", "name": "Казань"},
                                             "to": {"geo_id": "2656918", "name": "Сочи"}},
                            fetched_at="2026-10-25T12:00:00+00:00")
    assert offer is not None
    assert (offer.origin_city, offer.destination_city) == ("Казань", "Сочи")
    assert (offer.origin_provider_id, offer.destination_provider_id) == ("2656873", "2656918")


def test_hotel_price_is_whole_stay_for_children_and_unknown_pet_is_not_allowed():
    row = {"hotel_id": 44, "tutu_offer_id": "rate-1", "name": "Hotel",
           "best_offer": {"price": {"amount": 30600, "currency": "RUB"}, "breakfast_included": True}}
    details = {"stay": {"check_in": "2026-11-22", "check_out": "2026-11-29",
                         "guests": {"adults": 2, "children_ages": [5]}},
               "hotel": {"check_in_time": "14:00", "check_out_time": "12:00"}, "rooms": []}
    offer = hotel_offer(row, search_args={"check_in": "2026-11-22", "check_out": "2026-11-29",
                                         "adults": 2, "children_ages": [5]},
                       fetched_at="2026-10-25T12:00:00+00:00", details=details)
    assert offer is not None
    assert offer.total_rub == 30600  # not multiplied by seven nights or guest count
    assert offer.variant["terms"]["stay_nights"] == 7
    assert offer.variant["terms"]["guest_composition"] == {"adults": 2, "children_ages": [5]}
    assert offer.variant["terms"]["pet_allowed"] is None


def test_unknown_or_non_rub_price_is_not_normalized_as_free():
    missing = {"offer_id": "x", "departure_at": "2026-11-22T19:50:00+03:00",
               "arrival_at": "2026-11-23T19:50:00+03:00"}
    assert transport_offer(missing, kind="train", direction="outbound", search_args={},
                           fetched_at="2026-10-25T12:00:00+00:00") is None
    wrong_currency = {**missing, "price_party": {"amount": 0, "currency": "USD"}}
    assert transport_offer(wrong_currency, kind="train", direction="outbound", search_args={},
                           fetched_at="2026-10-25T12:00:00+00:00") is None


def test_details_rate_overrides_a_listing_price_that_moved_between_provider_calls():
    row = {"hotel_id": "hotel-7", "name": "Hotel", "best_offer": {
        "price": {"amount": 21500, "currency": "RUB"}, "free_cancellation": True,
    }}
    details = {"stay": {"check_in": "2026-11-22", "check_out": "2026-11-29",
                         "guests": {"adults": 2, "children_ages": [5]}},
               "hotel": {"check_in_time": "15:00:00", "check_out_time": "12:00:00"},
               "rooms": [{"room_name": "Family", "rates": [{"offerpack_hash": "rate-hash",
                    "price": {"amount": 20816, "currency": "RUB"}, "free_cancellation": False,
                    "breakfast_included": False}]}]}
    offer = hotel_offer(row, search_args={"check_in": "2026-11-22", "check_out": "2026-11-29",
                                         "adults": 2, "children_ages": [5]},
                       fetched_at="2026-10-25T12:00:00+00:00", details=details)
    assert offer is not None
    assert offer.total_rub == 20816
    assert offer.provider_offer_id == "rate-hash"
    assert offer.variant["terms"]["free_cancellation"] is False
    assert offer.variant["terms"]["offerpack_hash"] == "rate-hash"


def test_transport_conditions_are_taken_from_the_fare_matching_the_returned_price():
    row = {"offer_id": "flight-pair", "price": {"amount": 16359, "currency": "RUB"},
           "departure_at": "2026-11-22T18:15:00+03:00", "arrival_at": "2026-11-22T22:00:00+03:00",
           "legs": [{"label": "outbound", "segments": [{"departure_at": "2026-11-22T18:15:00+03:00",
                                                               "arrival_at": "2026-11-22T22:00:00+03:00"}]},
                    {"label": "return", "segments": [{"departure_at": "2026-11-29T17:10:00+03:00",
                                                            "arrival_at": "2026-11-29T21:00:00+03:00"}]}],
           "fares": [{"price": {"amount": 16359, "currency": "RUB"},
                      "conditions": {"baggage": {"kg": 0, "pieces": 0}, "refundable": False}},
                     {"price": {"amount": 22755, "currency": "RUB"},
                      "conditions": {"baggage": {"kg": 10, "pieces": 1}, "refundable": False}}]}
    offer = transport_offer(row, kind="flight", direction="round_trip", search_args={},
                            fetched_at="2026-10-25T12:00:00+00:00")
    assert offer is not None
    assert offer.variant["cancellation_status"] is False
    assert offer.variant["baggage"]["baggage"] == {"kg": 0, "pieces": 0}
    assert offer.variant["fare_conditions"]["baggage"]["kg"] == 0
