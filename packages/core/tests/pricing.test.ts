import { describe, expect, it } from "vitest";

import {
  calculateCost,
  createCatalog,
  getPricing,
  listPricing,
} from "../src/pricing.js";

describe("getPricing", () => {
  it("returns a known model", () => {
    const pricing = getPricing("gpt-4o");
    expect(pricing).not.toBeNull();
    expect(pricing?.provider).toBe("openai");
    expect(pricing?.inputPerMillion).toBeGreaterThan(0);
  });

  it("returns null for unknown models", () => {
    expect(getPricing("not-a-model")).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(getPricing("")).toBeNull();
  });
});

describe("listPricing", () => {
  it("includes at least one model from each supported provider", () => {
    const providers = new Set(listPricing().map((p) => p.provider));
    expect(providers.has("openai")).toBe(true);
    expect(providers.has("anthropic")).toBe(true);
    expect(providers.has("google")).toBe(true);
  });

  it("has unique model ids", () => {
    const models = listPricing().map((p) => p.model);
    expect(new Set(models).size).toBe(models.length);
  });

  it("has non-negative prices on every entry", () => {
    for (const entry of listPricing()) {
      expect(entry.inputPerMillion).toBeGreaterThanOrEqual(0);
      expect(entry.outputPerMillion).toBeGreaterThanOrEqual(0);
    }
  });
});

describe("calculateCost", () => {
  it("computes cost from per-million rates", () => {
    // gpt-4o at $2.50/$10.00 per million
    const cost = calculateCost("gpt-4o", 1_000_000, 1_000_000);
    expect(cost).toBe(12.5);
  });

  it("handles fractional token counts correctly", () => {
    // 1000 input + 500 output on gpt-4o
    // = (1000/1e6 * 2.5) + (500/1e6 * 10)
    // = 0.0025 + 0.005 = 0.0075
    const cost = calculateCost("gpt-4o", 1000, 500);
    expect(cost).toBe(0.0075);
  });

  it("returns 0 for unknown models instead of throwing", () => {
    expect(calculateCost("mystery-model", 100_000, 100_000)).toBe(0);
  });

  it("returns 0 for zero tokens", () => {
    expect(calculateCost("gpt-4o", 0, 0)).toBe(0);
  });

  it("rounds to six decimal places", () => {
    // 1 input token on gpt-4o-mini ($0.15 / 1M) = 1.5e-7
    // after rounding to 6 dp: 0
    expect(calculateCost("gpt-4o-mini", 1, 0)).toBe(0);
    // 10 input tokens = 1.5e-6 -> rounds to 0.000002
    expect(calculateCost("gpt-4o-mini", 10, 0)).toBe(0.000002);
  });

  it("throws RangeError on negative tokens", () => {
    expect(() => calculateCost("gpt-4o", -1, 0)).toThrow(RangeError);
    expect(() => calculateCost("gpt-4o", 0, -1)).toThrow(RangeError);
  });

  it("throws RangeError on non-finite tokens", () => {
    expect(() => calculateCost("gpt-4o", Number.NaN, 0)).toThrow(RangeError);
    expect(() => calculateCost("gpt-4o", Number.POSITIVE_INFINITY, 0)).toThrow(
      RangeError,
    );
  });

  it("handles very large token counts without overflow", () => {
    const cost = calculateCost("gpt-4o", 1_000_000_000, 1_000_000_000);
    expect(cost).toBe(12_500);
    expect(Number.isFinite(cost)).toBe(true);
  });
});

describe("createCatalog", () => {
  it("overrides default pricing with a custom catalog", () => {
    const catalog = createCatalog([
      {
        provider: "custom",
        model: "gpt-4o",
        inputPerMillion: 100,
        outputPerMillion: 200,
      },
    ]);
    expect(calculateCost("gpt-4o", 1_000_000, 1_000_000, catalog)).toBe(300);
  });

  it("returns null for models not in the custom catalog", () => {
    const catalog = createCatalog([
      {
        provider: "custom",
        model: "only-this",
        inputPerMillion: 1,
        outputPerMillion: 1,
      },
    ]);
    expect(catalog.get("gpt-4o")).toBeNull();
    expect(catalog.get("only-this")).not.toBeNull();
  });

  it("lists all entries in insertion order", () => {
    const entries = [
      { provider: "a", model: "a1", inputPerMillion: 1, outputPerMillion: 2 },
      { provider: "b", model: "b1", inputPerMillion: 3, outputPerMillion: 4 },
    ] as const;
    const catalog = createCatalog(entries);
    expect(catalog.list()).toEqual(entries);
  });
});
