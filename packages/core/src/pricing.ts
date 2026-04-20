import type { Pricing } from "./types.js";
import { DEFAULT_PRICING } from "./pricing-data.js";

const COST_DECIMAL_PLACES = 6;
const COST_SCALE = 10 ** COST_DECIMAL_PLACES;

const defaultIndex = buildIndex(DEFAULT_PRICING);

export interface PricingCatalog {
  get(model: string): Pricing | null;
  list(): readonly Pricing[];
}

export function createCatalog(entries: readonly Pricing[]): PricingCatalog {
  const index = buildIndex(entries);
  return {
    get: (model) => index.get(model) ?? null,
    list: () => entries,
  };
}

export function getPricing(model: string): Pricing | null {
  return defaultIndex.get(model) ?? null;
}

export function listPricing(): readonly Pricing[] {
  return DEFAULT_PRICING;
}

export function calculateCost(
  model: string,
  tokensInput: number,
  tokensOutput: number,
  catalog: PricingCatalog | null = null,
): number {
  assertNonNegative(tokensInput, "tokensInput");
  assertNonNegative(tokensOutput, "tokensOutput");

  const pricing = catalog === null ? getPricing(model) : catalog.get(model);
  if (pricing === null) return 0;

  const input = (tokensInput / 1_000_000) * pricing.inputPerMillion;
  const output = (tokensOutput / 1_000_000) * pricing.outputPerMillion;
  return roundUsd(input + output);
}

function buildIndex(entries: readonly Pricing[]): Map<string, Pricing> {
  const map = new Map<string, Pricing>();
  for (const entry of entries) {
    map.set(entry.model, entry);
  }
  return map;
}

function assertNonNegative(value: number, name: string): void {
  if (!Number.isFinite(value) || value < 0) {
    throw new RangeError(`${name} must be a non-negative finite number, got ${value}`);
  }
}

// Fixed-precision rounding avoids floating-point drift when summing costs
// across many spans. Six decimal places = fractions of a micro-USD, plenty
// for per-token pricing where the smallest unit is ~$1e-7.
function roundUsd(value: number): number {
  return Math.round(value * COST_SCALE) / COST_SCALE;
}
