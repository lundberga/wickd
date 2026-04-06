import { describe, it, expect } from "vitest";
import { getPrice, calculateCost, FALLBACK_PRICE } from "wickd-core";

describe("getPrice", () => {
  it("returns exact match", () => {
    const p = getPrice("gpt-4o");
    expect(p.provider).toBe("openai");
    expect(p.inputPer1M).toBe(2.5);
  });

  it("returns anthropic model", () => {
    const p = getPrice("claude-sonnet-4-6");
    expect(p.provider).toBe("anthropic");
  });

  it("prefix matches", () => {
    const p = getPrice("gpt-4o-2024-08-06");
    expect(p.provider).toBe("openai");
    expect(p.inputPer1M).toBe(2.5);
  });

  it("falls back for unknown model", () => {
    expect(getPrice("unknown-model")).toBe(FALLBACK_PRICE);
  });
});

describe("calculateCost", () => {
  it("zero tokens costs zero", () => {
    expect(calculateCost("gpt-4o", 0, 0)).toBe(0);
  });

  it("calculates known model", () => {
    // gpt-4o: $2.50/1M in, $10.00/1M out
    const cost = calculateCost("gpt-4o", 1000, 500);
    const expected = (1000 / 1e6) * 2.5 + (500 / 1e6) * 10;
    expect(cost).toBeCloseTo(expected, 6);
  });

  it("uses fallback for unknown", () => {
    const cost = calculateCost("nonexistent", 1000, 1000);
    const expected = (1000 / 1e6) * 10 + (1000 / 1e6) * 30;
    expect(cost).toBeCloseTo(expected, 6);
  });
});
