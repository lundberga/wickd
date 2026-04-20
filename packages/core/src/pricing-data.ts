import type { Pricing } from "./types.js";

// Best-effort pricing in USD per million tokens. Last verified 2026-04-20
// against openai.com/pricing, anthropic.com/pricing, ai.google.dev/pricing.
// Consumers can override by providing their own pricing table; see pricing.ts.
//
// Entries are ordered by provider, then input price ascending.
export const DEFAULT_PRICING: readonly Pricing[] = [
  // OpenAI
  { provider: "openai", model: "gpt-4o-mini", inputPerMillion: 0.15, outputPerMillion: 0.6 },
  { provider: "openai", model: "gpt-3.5-turbo", inputPerMillion: 0.5, outputPerMillion: 1.5 },
  { provider: "openai", model: "gpt-4o", inputPerMillion: 2.5, outputPerMillion: 10 },
  { provider: "openai", model: "o1-mini", inputPerMillion: 3, outputPerMillion: 12 },
  { provider: "openai", model: "gpt-4-turbo", inputPerMillion: 10, outputPerMillion: 30 },
  { provider: "openai", model: "o1", inputPerMillion: 15, outputPerMillion: 60 },

  // Anthropic
  { provider: "anthropic", model: "claude-haiku-4-5", inputPerMillion: 0.8, outputPerMillion: 4 },
  { provider: "anthropic", model: "claude-3-5-haiku-20241022", inputPerMillion: 0.8, outputPerMillion: 4 },
  { provider: "anthropic", model: "claude-sonnet-4-6", inputPerMillion: 3, outputPerMillion: 15 },
  { provider: "anthropic", model: "claude-3-5-sonnet-20241022", inputPerMillion: 3, outputPerMillion: 15 },
  { provider: "anthropic", model: "claude-opus-4-7", inputPerMillion: 15, outputPerMillion: 75 },
  { provider: "anthropic", model: "claude-3-opus-20240229", inputPerMillion: 15, outputPerMillion: 75 },

  // Google
  { provider: "google", model: "gemini-1.5-flash", inputPerMillion: 0.075, outputPerMillion: 0.3 },
  { provider: "google", model: "gemini-2.0-flash", inputPerMillion: 0.1, outputPerMillion: 0.4 },
  { provider: "google", model: "gemini-1.5-pro", inputPerMillion: 1.25, outputPerMillion: 5 },
];
