from app.product_scope import coverage_payload, destination_supported, fixed_destination_id


def test_current_product_coverage_is_one_configured_rest_destination():
    payload = coverage_payload()
    assert fixed_destination_id() == "AER"
    assert payload["destinations"] == [{"id": "AER", "name": "Сочи"}]
    assert payload["destination_selection_required"] is False
    assert destination_supported("Сочи") is True
    assert destination_supported("Дубай") is False
