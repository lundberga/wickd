import { describe, it, expect } from "vitest";
import { Budget, BudgetTracker, BudgetExceeded } from "../src/budget.js";

/**
 * Property-based tests for the Wickd Lyapunov invariant.
 *
 * The invariant: V(t) = min(C_run - c(t), C_daily - c_d(t), C_monthly - c_m(t)) >= 0
 * at all times before a BudgetExceeded is raised.
 */

/**
 * Simple seeded PRNG (mulberry32) for reproducible random sequences.
 */
function mulberry32(seed: number): () => number {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * Compute the Lyapunov energy function V(t) for a tracker.
 * Returns the minimum remaining budget across all configured caps.
 * Returns Infinity if no caps are configured.
 */
function computeV(tracker: BudgetTracker): number {
  const remainders: number[] = [];
  if (tracker.budget.perRun != null) remainders.push(tracker.budget.perRun - tracker.runSpend);
  if (tracker.budget.daily != null) remainders.push(tracker.budget.daily - tracker.dailySpend);
  // monthlySpend is not directly exposed, but summary() provides it
  const summary = tracker.summary();
  if (tracker.budget.monthly != null) remainders.push(tracker.budget.monthly - summary.monthlySpend);
  return remainders.length > 0 ? Math.min(...remainders) : Infinity;
}

describe("Lyapunov invariant: V(t) >= 0 before BudgetExceeded", () => {
  it("invariant holds across 100 random cost sequences", () => {
    const rng = mulberry32(42);

    for (let trial = 0; trial < 100; trial++) {
      // Random caps between 0.01 and 5.0
      const perRun = rng() * 4.99 + 0.01;
      const daily = rng() * 4.99 + 0.01;
      const monthly = rng() * 4.99 + 0.01;

      const tracker = new BudgetTracker(new Budget({ perRun, daily, monthly }));

      // Random sequence of 1..50 costs
      const numCosts = Math.floor(rng() * 50) + 1;
      let budgetExceeded = false;

      for (let i = 0; i < numCosts; i++) {
        // Before each call, V(t) must be >= 0 if we haven't been killed
        if (!budgetExceeded) {
          const v = computeV(tracker);
          expect(v).toBeGreaterThanOrEqual(-1e-9); // allow tiny FP epsilon
        }

        const cost = rng() * 0.5; // individual costs between 0 and 0.50
        try {
          tracker.recordCost(cost);
        } catch (e) {
          expect(e).toBeInstanceOf(BudgetExceeded);
          budgetExceeded = true;
        }
      }
    }
  });

  it("cost accumulation is monotonically non-decreasing", () => {
    const rng = mulberry32(123);
    const tracker = new BudgetTracker(new Budget({ perRun: 100 }));

    let previousSpend = 0;
    for (let i = 0; i < 200; i++) {
      const cost = rng() * 0.5;
      tracker.recordCost(cost);
      expect(tracker.runSpend).toBeGreaterThanOrEqual(previousSpend);
      previousSpend = tracker.runSpend;
    }
  });

  it("V(t) = 0 triggers immediate halt", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.10 }));

    // Record costs that sum exactly to the cap
    tracker.recordCost(0.04);
    tracker.recordCost(0.03);
    // At this point, runSpend = 0.07, remaining = 0.03
    expect(tracker.runSpend).toBeCloseTo(0.07, 10);

    // This brings us to exactly 0.10 -- should trigger BudgetExceeded
    expect(() => tracker.recordCost(0.03)).toThrow(BudgetExceeded);
    expect(tracker.isKilled).toBe(true);
  });

  it("kill prevents all further calls", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.05 }));

    // Exceed the budget
    expect(() => tracker.recordCost(0.10)).toThrow(BudgetExceeded);
    expect(tracker.isKilled).toBe(true);

    // preCallCheck should also throw
    expect(() => tracker.preCallCheck()).toThrow(BudgetExceeded);

    // recordCost should also throw (via checkBudget internally)
    expect(() => tracker.recordCost(0.001)).toThrow(BudgetExceeded);
  });

  it("tightest cap is enforced (daily < perRun)", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 1.0, daily: 0.05, monthly: 10.0 }));

    // Record a cost that exceeds daily (0.05) but not perRun (1.0) or monthly (10.0)
    expect(() => tracker.recordCost(0.06)).toThrow(BudgetExceeded);

    // Verify it was the daily cap that triggered
    try {
      // Reset and try again to inspect the error
      const tracker2 = new BudgetTracker(new Budget({ perRun: 1.0, daily: 0.05, monthly: 10.0 }));
      tracker2.recordCost(0.06);
    } catch (e) {
      expect(e).toBeInstanceOf(BudgetExceeded);
      expect((e as BudgetExceeded).trigger).toBe("daily");
      expect((e as BudgetExceeded).spent).toBeCloseTo(0.06, 10);
    }
  });

  it("energy function values are correct", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 2.0, daily: 5.0, monthly: 20.0 }));

    // Before any costs, V(t) should equal the smallest cap
    expect(computeV(tracker)).toBeCloseTo(2.0, 10); // min(2.0, 5.0, 20.0)

    tracker.recordCost(0.50);
    // V(t) = min(2.0 - 0.50, 5.0 - 0.50, 20.0 - 0.50) = min(1.50, 4.50, 19.50) = 1.50
    expect(computeV(tracker)).toBeCloseTo(1.50, 10);

    tracker.recordCost(0.75);
    // V(t) = min(2.0 - 1.25, 5.0 - 1.25, 20.0 - 1.25) = min(0.75, 3.75, 18.75) = 0.75
    expect(computeV(tracker)).toBeCloseTo(0.75, 10);

    tracker.recordCost(0.50);
    // V(t) = min(2.0 - 1.75, 5.0 - 1.75, 20.0 - 1.75) = min(0.25, 3.25, 18.25) = 0.25
    expect(computeV(tracker)).toBeCloseTo(0.25, 10);

    // Also verify remaining() agrees
    expect(tracker.remaining()).toBeCloseTo(0.25, 10);

    // Verify the summary values
    const summary = tracker.summary();
    expect(summary.runSpend).toBeCloseTo(1.75, 10);
    expect(summary.dailySpend).toBeCloseTo(1.75, 10);
    expect(summary.monthlySpend).toBeCloseTo(1.75, 10);
    expect(summary.callCount).toBe(3);
  });
});
