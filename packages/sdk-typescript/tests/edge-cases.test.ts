import { describe, it, expect, vi } from "vitest";
import { Budget, BudgetTracker, BudgetExceeded } from "../src/budget.js";

// ── Budget: exact and floating-point boundary ─────────────────────────────

describe("Budget boundaries", () => {
  it("cost exactly at perRun cap triggers BudgetExceeded", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.10 }));
    expect(() => tracker.recordCost(0.10)).toThrow(BudgetExceeded);
  });

  it("cost just below cap does not trigger BudgetExceeded", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.10 }));
    expect(() => tracker.recordCost(0.09999)).not.toThrow();
    expect(tracker.isKilled).toBe(false);
  });

  it("floating point accumulation crossing cap triggers BudgetExceeded", () => {
    // 0.1 + 0.1 + 0.1 == 0.30000000000000004 in IEEE 754 — crosses 0.30
    const tracker = new BudgetTracker(new Budget({ perRun: 0.30 }));
    tracker.recordCost(0.10);
    tracker.recordCost(0.10);
    expect(() => tracker.recordCost(0.10)).toThrow(BudgetExceeded);
  });

  it("daily cap at exact boundary triggers BudgetExceeded", () => {
    const tracker = new BudgetTracker(new Budget({ daily: 1.00 }));
    expect(() => tracker.recordCost(1.00)).toThrow(BudgetExceeded);
  });

  it("monthly cap at exact boundary triggers BudgetExceeded", () => {
    const tracker = new BudgetTracker(new Budget({ monthly: 5.00 }));
    expect(() => tracker.recordCost(5.00)).toThrow(BudgetExceeded);
  });

  it("zero cost never triggers any cap", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.01 }));
    for (let i = 0; i < 100; i++) tracker.recordCost(0);
    expect(tracker.runSpend).toBe(0);
    expect(tracker.callCount).toBe(100);
    expect(tracker.isKilled).toBe(false);
  });

  it("tightest cap governs when multiple caps are set", () => {
    // daily=0.50 fires before perRun=10.0
    const tracker = new BudgetTracker(new Budget({ perRun: 10.0, daily: 0.50 }));
    let err: BudgetExceeded | undefined;
    try { tracker.recordCost(0.50); } catch (e) { err = e as BudgetExceeded; }
    expect(err).toBeInstanceOf(BudgetExceeded);
    // The trigger is the cap that was checked first in checkBudget: perRun, then daily
    // At 0.50 spend: perRun(10.0) not exceeded, daily(0.50) is >= => daily fires
    expect(err?.trigger).toBe("daily");
  });

  it("remaining goes negative after overshoot", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.10 }));
    try { tracker.recordCost(0.20); } catch { /* expected */ }
    expect(tracker.remaining()!).toBeLessThan(0);
  });

  it("remaining is null when no caps are set", () => {
    const tracker = new BudgetTracker(new Budget({}));
    tracker.recordCost(9999);
    expect(tracker.remaining()).toBeNull();
  });
});

// ── Budget: killed state ──────────────────────────────────────────────────

describe("BudgetTracker killed state", () => {
  it("preCallCheck after kill throws with trigger already_killed", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.01 }));
    try { tracker.recordCost(0.02); } catch { /* expected */ }
    expect(tracker.isKilled).toBe(true);
    expect(() => tracker.preCallCheck()).toThrow(BudgetExceeded);
    let err: BudgetExceeded | undefined;
    try { tracker.preCallCheck(); } catch (e) { err = e as BudgetExceeded; }
    expect(err?.trigger).toBe("already_killed");
  });

  it("summary reflects killed state after budget exceeded", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.01 }));
    try { tracker.recordCost(0.05); } catch { /* expected */ }
    const s = tracker.summary();
    expect(s.killed).toBe(true);
    expect(s.runSpend).toBeGreaterThanOrEqual(0.05);
  });

  it("resetRun clears killed flag and allows further calls", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.01 }));
    try { tracker.recordCost(0.05); } catch { /* expected */ }
    tracker.resetRun();
    expect(tracker.isKilled).toBe(false);
    expect(() => tracker.recordCost(0.005)).not.toThrow();
  });

  it("daily spend persists across resetRun", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 1, daily: 10 }));
    tracker.recordCost(0.50);
    tracker.resetRun();
    expect(tracker.runSpend).toBe(0);
    expect(tracker.dailySpend).toBe(0.50);
  });
});

// ── Budget: onKill callback ───────────────────────────────────────────────

describe("BudgetTracker onKill", () => {
  it("onKill receives accurate summary at time of kill", () => {
    let received: ReturnType<BudgetTracker["summary"]> | null = null;
    const budget = new Budget({ perRun: 0.10, onKill: (s) => { received = s; } });
    const tracker = new BudgetTracker(budget);
    try { tracker.recordCost(0.15); } catch { /* expected */ }
    expect(received).not.toBeNull();
    expect(received!.killed).toBe(true);
    expect(received!.runSpend).toBeGreaterThanOrEqual(0.15);
    expect(received!.caps.perRun).toBe(0.10);
  });

  it("exception thrown in onKill does not mask BudgetExceeded", () => {
    const budget = new Budget({
      perRun: 0.01,
      onKill: () => { throw new Error("handler boom"); },
    });
    const tracker = new BudgetTracker(budget);
    expect(() => tracker.recordCost(0.05)).toThrow(BudgetExceeded);
  });

  it("onKill is not called when under budget", () => {
    const kill = vi.fn();
    const tracker = new BudgetTracker(new Budget({ perRun: 1.0, onKill: kill }));
    tracker.recordCost(0.50);
    expect(kill).not.toHaveBeenCalled();
  });
});

// ── Budget: validation ────────────────────────────────────────────────────

describe("Budget validation", () => {
  it("zero perRun is rejected", () => {
    expect(() => new Budget({ perRun: 0 })).toThrow("perRun");
  });

  it("negative daily is rejected", () => {
    expect(() => new Budget({ daily: -0.001 })).toThrow("daily");
  });

  it("very small positive cap is accepted", () => {
    const b = new Budget({ perRun: 0.0001 });
    expect(b.perRun).toBeCloseTo(0.0001);
  });

  it("BudgetExceeded carries correct metadata", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.50 }));
    let err: BudgetExceeded | undefined;
    try { tracker.recordCost(0.75); } catch (e) { err = e as BudgetExceeded; }
    expect(err).toBeInstanceOf(BudgetExceeded);
    expect(err?.trigger).toBe("perRun");
    expect(err?.spent).toBeGreaterThanOrEqual(0.75);
    expect(err?.name).toBe("BudgetExceeded");
  });
});

// ── Budget: summary ───────────────────────────────────────────────────────

describe("BudgetTracker summary", () => {
  it("caps object reflects original budget config", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 2, daily: 10, monthly: 50 }));
    const s = tracker.summary();
    expect(s.caps.perRun).toBe(2);
    expect(s.caps.daily).toBe(10);
    expect(s.caps.monthly).toBe(50);
  });

  it("summary caps are undefined when not set", () => {
    const tracker = new BudgetTracker(new Budget({}));
    const s = tracker.summary();
    expect(s.caps.perRun).toBeUndefined();
    expect(s.caps.daily).toBeUndefined();
    expect(s.caps.monthly).toBeUndefined();
  });

  it("callCount increments once per recordCost even when BudgetExceeded is thrown", () => {
    // BudgetExceeded is thrown after incrementing the count
    const tracker = new BudgetTracker(new Budget({ perRun: 0.01 }));
    try { tracker.recordCost(0.05); } catch { /* expected */ }
    expect(tracker.callCount).toBe(1);
  });
});
