import pytest
from wickd.pricing import calculate_cost, get_price, FALLBACK_PRICE


class TestGetPrice:
    def test_exact_match(self):
        price = get_price("gpt-4o")
        assert price.provider == "openai"
        assert price.input_per_1m == 2.50

    def test_anthropic(self):
        price = get_price("claude-sonnet-4-6")
        assert price.provider == "anthropic"
        assert price.input_per_1m == 3.00

    def test_prefix_match(self):
        price = get_price("gpt-4o-2024-08-06")
        assert price.provider == "openai"
        assert price.input_per_1m == 2.50

    def test_unknown_falls_back(self):
        assert get_price("some-unknown-model-v99") == FALLBACK_PRICE

    def test_google(self):
        assert get_price("gemini-2.5-pro").provider == "google"


class TestCalculateCost:
    def test_zero_tokens(self):
        assert calculate_cost("gpt-4o", 0, 0) == 0.0

    def test_known_model(self):
        cost = calculate_cost("gpt-4o", 1000, 500)
        expected = (1000 / 1_000_000) * 2.50 + (500 / 1_000_000) * 10.00
        assert cost == round(expected, 6)

    def test_unknown_uses_fallback(self):
        cost = calculate_cost("nonexistent-model", 1000, 1000)
        expected = (1000 / 1_000_000) * 10.00 + (1000 / 1_000_000) * 30.00
        assert cost == round(expected, 6)

    def test_large_token_count(self):
        assert calculate_cost("gpt-4o", 1_000_000, 1_000_000) == 2.50 + 10.00
