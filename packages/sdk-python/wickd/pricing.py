from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelPrice:
    input_per_1m: float   # USD per 1M input tokens
    output_per_1m: float  # USD per 1M output tokens
    provider: str


# Prices as of April 2026. Kept conservative (overestimates slightly).
MODEL_PRICES: dict[str, ModelPrice] = {
    # OpenAI
    "gpt-4o":                       ModelPrice(2.50, 10.00, "openai"),
    "gpt-4o-mini":                  ModelPrice(0.15, 0.60, "openai"),
    "gpt-4o-2024-11-20":            ModelPrice(2.50, 10.00, "openai"),
    "gpt-4-turbo":                  ModelPrice(10.00, 30.00, "openai"),
    "gpt-4":                        ModelPrice(30.00, 60.00, "openai"),
    "gpt-3.5-turbo":                ModelPrice(0.50, 1.50, "openai"),
    "o1":                           ModelPrice(15.00, 60.00, "openai"),
    "o1-mini":                      ModelPrice(3.00, 12.00, "openai"),
    "o1-pro":                       ModelPrice(150.00, 600.00, "openai"),
    "o3":                           ModelPrice(10.00, 40.00, "openai"),
    "o3-mini":                      ModelPrice(1.10, 4.40, "openai"),
    "o4-mini":                      ModelPrice(1.10, 4.40, "openai"),
    "gpt-4.1":                      ModelPrice(2.00, 8.00, "openai"),
    "gpt-4.1-mini":                 ModelPrice(0.40, 1.60, "openai"),
    "gpt-4.1-nano":                 ModelPrice(0.10, 0.40, "openai"),
    # Anthropic
    "claude-sonnet-4-6":            ModelPrice(3.00, 15.00, "anthropic"),
    "claude-opus-4-6":              ModelPrice(15.00, 75.00, "anthropic"),
    "claude-haiku-4-5":             ModelPrice(0.80, 4.00, "anthropic"),
    "claude-3-5-sonnet-20241022":   ModelPrice(3.00, 15.00, "anthropic"),
    "claude-3-5-haiku-20241022":    ModelPrice(0.80, 4.00, "anthropic"),
    "claude-3-opus-20240229":       ModelPrice(15.00, 75.00, "anthropic"),
    # Google
    "gemini-2.0-flash":             ModelPrice(0.10, 0.40, "google"),
    "gemini-2.0-pro":               ModelPrice(1.25, 10.00, "google"),
    "gemini-2.5-pro":               ModelPrice(1.25, 10.00, "google"),
    "gemini-2.5-flash":             ModelPrice(0.15, 0.60, "google"),
}

# Fallback for unknown models -- expensive estimate to be safe
FALLBACK_PRICE = ModelPrice(10.00, 30.00, "unknown")


def get_price(model: str) -> ModelPrice:
    """Look up pricing. Falls back to conservative estimate for unknown models."""
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]

    # Prefix match (e.g. "gpt-4o-2024-08-06" -> "gpt-4o")
    for known in sorted(MODEL_PRICES, key=len, reverse=True):
        if model.startswith(known):
            return MODEL_PRICES[known]

    return FALLBACK_PRICE


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate USD cost of an LLM call."""
    price = get_price(model)
    return round(
        (input_tokens / 1_000_000) * price.input_per_1m +
        (output_tokens / 1_000_000) * price.output_per_1m,
        6,
    )
