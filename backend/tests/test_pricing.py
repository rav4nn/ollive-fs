from app.services.pricing import estimate_cost


def test_zero_tokens_is_zero_cost():
    assert estimate_cost(0, 0) == 0.0


def test_input_only_cost():
    # 1M input tokens at $3.00 default = $3.00
    assert estimate_cost(1_000_000, 0) == 3.00


def test_output_only_cost():
    assert estimate_cost(0, 1_000_000) == 15.00


def test_mixed_cost_rounded():
    cost = estimate_cost(1234, 567)
    assert cost > 0
    assert round(cost, 6) == cost
