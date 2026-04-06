import { describe, it, expect } from "vitest";
import { Budget, BudgetTracker, BudgetExceeded } from "../src/budget.js";

describe("Budget", () => {
  it("accepts valid config", () => {
    const b = new Budget({ perRun: 2.0, daily: 20.0, monthly: 100.0 });
    expect(b.perRun).toBe(2.0);
    expect(b.daily).toBe(20.0);
    expect(b.monthly).toBe(100.0);
  });

  it("allows no caps", () => {
    const b = new Budget({});
    expect(b.perRun).toBeUndefined();
  });

  it("rejects negative perRun", () => {
    expect(() => new Budget({ perRun: -1 })).toThrow("perRun");
  });

  it("rejects zero daily", () => {
    expect(() => new Budget({ daily: 0 })).toThrow("daily");
  });
});

describe("BudgetTracker", () => {
  it("records cost", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 10 }));
    tracker.recordCost(1.5, "gpt-4o", 100, 50);
    expect(tracker.runSpend).toBe(1.5);
    expect(tracker.callCount).toBe(1);
  });

  it("computes remaining", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 5 }));
    tracker.recordCost(2, "gpt-4o", 100, 50);
    expect(tracker.remaining()).toBe(3);
  });

  it("remaining is null with no caps", () => {
    const tracker = new BudgetTracker(new Budget({}));
    tracker.recordCost(100, "gpt-4o", 100, 50);
    expect(tracker.remaining()).toBeNull();
  });

  it("throws BudgetExceeded on perRun", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 1 }));
    expect(() => tracker.recordCost(1.5, "gpt-4o", 100, 50)).toThrow(BudgetExceeded);
  });

  it("throws BudgetExceeded on daily", () => {
    const tracker = new BudgetTracker(new Budget({ daily: 2 }));
    tracker.recordCost(1, "gpt-4o", 100, 50);
    expect(() => tracker.recordCost(1.5, "gpt-4o", 100, 50)).toThrow(BudgetExceeded);
  });

  it("pre-call check after kill throws", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 1 }));
    expect(() => tracker.recordCost(2, "gpt-4o", 100, 50)).toThrow(BudgetExceeded);
    expect(tracker.isKilled).toBe(true);
    expect(() => tracker.preCallCheck()).toThrow(BudgetExceeded);
  });

  it("resets run", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 1, daily: 10 }));
    tracker.recordCost(0.5, "gpt-4o", 100, 50);
    tracker.resetRun();
    expect(tracker.runSpend).toBe(0);
    expect(tracker.callCount).toBe(0);
    expect(tracker.dailySpend).toBe(0.5);
  });

  it("fires onKill callback", () => {
    let killed: any = null;
    const budget = new Budget({ perRun: 0.1, onKill: (s) => { killed = s; } });
    const tracker = new BudgetTracker(budget);
    expect(() => tracker.recordCost(0.2, "gpt-4o", 100, 50)).toThrow(BudgetExceeded);
    expect(killed).not.toBeNull();
    expect(killed.killed).toBe(true);
  });

  it("produces summary", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 5, daily: 20 }));
    tracker.recordCost(1, "gpt-4o", 100, 50);
    const s = tracker.summary();
    expect(s.runSpend).toBe(1);
    expect(s.callCount).toBe(1);
    expect(s.killed).toBe(false);
    expect(s.caps.perRun).toBe(5);
  });
});
