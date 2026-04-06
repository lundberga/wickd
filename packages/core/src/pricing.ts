/**
 * Model pricing database.
 *
 * Prices are per 1M tokens (input/output) in USD.
 * Kept in sync with the Python SDK's pricing.py.
 */

export interface ModelPrice {
  inputPer1M: number;
  outputPer1M: number;
  provider: string;
}

/** Pricing as of April 2026 — kept conservative (overestimates slightly). */
export const MODEL_PRICES: Record<string, ModelPrice> = {
  // OpenAI
  "gpt-4o":                     { inputPer1M: 2.50,   outputPer1M: 10.00,  provider: "openai" },
  "gpt-4o-mini":                { inputPer1M: 0.15,   outputPer1M: 0.60,   provider: "openai" },
  "gpt-4o-2024-11-20":          { inputPer1M: 2.50,   outputPer1M: 10.00,  provider: "openai" },
  "gpt-4-turbo":                { inputPer1M: 10.00,  outputPer1M: 30.00,  provider: "openai" },
  "gpt-4":                      { inputPer1M: 30.00,  outputPer1M: 60.00,  provider: "openai" },
  "gpt-3.5-turbo":              { inputPer1M: 0.50,   outputPer1M: 1.50,   provider: "openai" },
  "o1":                         { inputPer1M: 15.00,  outputPer1M: 60.00,  provider: "openai" },
  "o1-mini":                    { inputPer1M: 3.00,   outputPer1M: 12.00,  provider: "openai" },
  "o1-pro":                     { inputPer1M: 150.00, outputPer1M: 600.00, provider: "openai" },
  "o3":                         { inputPer1M: 10.00,  outputPer1M: 40.00,  provider: "openai" },
  "o3-mini":                    { inputPer1M: 1.10,   outputPer1M: 4.40,   provider: "openai" },
  "o4-mini":                    { inputPer1M: 1.10,   outputPer1M: 4.40,   provider: "openai" },
  "gpt-4.1":                    { inputPer1M: 2.00,   outputPer1M: 8.00,   provider: "openai" },
  "gpt-4.1-mini":               { inputPer1M: 0.40,   outputPer1M: 1.60,   provider: "openai" },
  "gpt-4.1-nano":               { inputPer1M: 0.10,   outputPer1M: 0.40,   provider: "openai" },

  // Anthropic
  "claude-sonnet-4-6":          { inputPer1M: 3.00,   outputPer1M: 15.00,  provider: "anthropic" },
  "claude-opus-4-6":            { inputPer1M: 15.00,  outputPer1M: 75.00,  provider: "anthropic" },
  "claude-haiku-4-5":           { inputPer1M: 0.80,   outputPer1M: 4.00,   provider: "anthropic" },
  "claude-3-5-sonnet-20241022": { inputPer1M: 3.00,   outputPer1M: 15.00,  provider: "anthropic" },
  "claude-3-5-haiku-20241022":  { inputPer1M: 0.80,   outputPer1M: 4.00,   provider: "anthropic" },
  "claude-3-opus-20240229":     { inputPer1M: 15.00,  outputPer1M: 75.00,  provider: "anthropic" },

  // Google
  "gemini-2.0-flash":           { inputPer1M: 0.10,   outputPer1M: 0.40,   provider: "google" },
  "gemini-2.0-pro":             { inputPer1M: 1.25,   outputPer1M: 10.00,  provider: "google" },
  "gemini-2.5-pro":             { inputPer1M: 1.25,   outputPer1M: 10.00,  provider: "google" },
  "gemini-2.5-flash":           { inputPer1M: 0.15,   outputPer1M: 0.60,   provider: "google" },
};

/** Fallback for unknown models — uses expensive estimate to be safe. */
export const FALLBACK_PRICE: ModelPrice = {
  inputPer1M: 10.00,
  outputPer1M: 30.00,
  provider: "unknown",
};

/** Get pricing for a model. Falls back to conservative estimate for unknown models. */
export function getPrice(model: string): ModelPrice {
  if (model in MODEL_PRICES) {
    return MODEL_PRICES[model];
  }

  // Prefix matching (e.g. "gpt-4o-2024-08-06" → "gpt-4o")
  const sorted = Object.keys(MODEL_PRICES).sort((a, b) => b.length - a.length);
  for (const known of sorted) {
    if (model.startsWith(known)) {
      return MODEL_PRICES[known];
    }
  }

  return FALLBACK_PRICE;
}

/** Calculate the cost of an LLM call in USD. */
export function calculateCost(
  model: string,
  inputTokens: number,
  outputTokens: number,
): number {
  const price = getPrice(model);
  const inputCost = (inputTokens / 1_000_000) * price.inputPer1M;
  const outputCost = (outputTokens / 1_000_000) * price.outputPer1M;
  return Math.round((inputCost + outputCost) * 1_000_000) / 1_000_000;
}
