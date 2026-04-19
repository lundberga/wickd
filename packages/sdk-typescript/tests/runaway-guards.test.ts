import { describe, it, expect, beforeEach } from "vitest";
import { Budget, BudgetTracker, BudgetExceeded } from "../src/budget.js";

describe("Budget runaway-guard validation", () => {
  it("rejects zero maxLlmCalls", () => {
    expect(() => new Budget({ maxLlmCalls: 0 })).toThrow("maxLlmCalls");
  });

  it("rejects negative maxLlmCalls", () => {
    expect(() => new Budget({ maxLlmCalls: -1 })).toThrow("maxLlmCalls");
  });

  it("rejects non-integer maxToolCalls", () => {
    expect(() => new Budget({ maxToolCalls: 2.5 })).toThrow("maxToolCalls");
  });

  it("rejects zero maxDurationSeconds", () => {
    expect(() => new Budget({ maxDurationSeconds: 0 })).toThrow("maxDurationSeconds");
  });

  it("all guards optional", () => {
    const b = new Budget({});
    expect(b.maxLlmCalls).toBeUndefined();
    expect(b.maxToolCalls).toBeUndefined();
    expect(b.maxDurationSeconds).toBeUndefined();
  });
});

describe("BudgetTracker maxLlmCalls", () => {
  it("kills at cap", () => {
    const tracker = new BudgetTracker(new Budget({ maxLlmCalls: 3 }));
    tracker.recordCost(0.001);
    tracker.recordCost(0.001);
    expect(() => tracker.recordCost(0.001)).toThrow(BudgetExceeded);
  });

  it("trigger and measured populated", () => {
    const tracker = new BudgetTracker(new Budget({ maxLlmCalls: 2 }));
    tracker.recordCost(0.001);
    try {
      tracker.recordCost(0.001);
      throw new Error("expected BudgetExceeded");
    } catch (err) {
      expect(err).toBeInstanceOf(BudgetExceeded);
      const be = err as BudgetExceeded;
      expect(be.trigger).toBe("max_llm_calls");
      expect(be.measured).toBe(2);
      expect(be.message).toContain("calls");
    }
  });

  it("allows under cap", () => {
    const tracker = new BudgetTracker(new Budget({ maxLlmCalls: 5 }));
    for (let i = 0; i < 4; i++) tracker.recordCost(0.01);
    expect(tracker.callCount).toBe(4);
    expect(tracker.isKilled).toBe(false);
  });
});

describe("BudgetTracker maxToolCalls", () => {
  it("kills at cap", () => {
    const tracker = new BudgetTracker(new Budget({ maxToolCalls: 2 }));
    tracker.recordToolCall();
    try {
      tracker.recordToolCall();
      throw new Error("expected");
    } catch (err) {
      expect(err).toBeInstanceOf(BudgetExceeded);
      expect((err as BudgetExceeded).trigger).toBe("max_tool_calls");
    }
  });

  it("is independent of LLM call count", () => {
    const tracker = new BudgetTracker(new Budget({ maxToolCalls: 1, maxLlmCalls: 10 }));
    for (let i = 0; i < 5; i++) tracker.recordCost(0.001);
    expect(() => tracker.recordToolCall()).toThrow(/max_tool_calls/);
  });
});

describe("BudgetTracker maxDurationSeconds", () => {
  it("kills once elapsed", async () => {
    const tracker = new BudgetTracker(new Budget({ maxDurationSeconds: 0.05 }));
    await new Promise(r => setTimeout(r, 60));
    try {
      tracker.recordCost(0.001);
      throw new Error("expected");
    } catch (err) {
      expect(err).toBeInstanceOf(BudgetExceeded);
      expect((err as BudgetExceeded).trigger).toBe("max_duration");
    }
  });

  it("tool call triggers duration check too", async () => {
    const tracker = new BudgetTracker(new Budget({ maxDurationSeconds: 0.05 }));
    await new Promise(r => setTimeout(r, 60));
    expect(() => tracker.recordToolCall()).toThrow(/max_duration/);
  });

  it("elapsedSeconds grows monotonically", async () => {
    const tracker = new BudgetTracker(new Budget({}));
    await new Promise(r => setTimeout(r, 20));
    expect(tracker.elapsedSeconds).toBeGreaterThanOrEqual(0.02);
  });
});

describe("Combined caps", () => {
  it("cost cap wins when tight", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.02, maxLlmCalls: 100 }));
    try {
      tracker.recordCost(0.03);
      throw new Error("expected");
    } catch (err) {
      expect((err as BudgetExceeded).trigger).toBe("perRun");
    }
  });

  it("whichever trips first wins", () => {
    const tracker = new BudgetTracker(new Budget({ perRun: 0.02, maxLlmCalls: 10 }));
    tracker.recordCost(0.01);
    try {
      tracker.recordCost(0.02);
      throw new Error("expected");
    } catch (err) {
      expect((err as BudgetExceeded).trigger).toBe("perRun");
    }
  });
});

describe("Summary and reset", () => {
  it("summary exposes new counters", () => {
    const tracker = new BudgetTracker(
      new Budget({ maxLlmCalls: 5, maxToolCalls: 10, maxDurationSeconds: 60 }),
    );
    tracker.recordCost(0.01);
    tracker.recordToolCall();
    const s = tracker.summary();
    expect(s.callCount).toBe(1);
    expect(s.toolCallCount).toBe(1);
    expect(s.elapsedSeconds).toBeGreaterThanOrEqual(0);
    expect(s.caps.maxLlmCalls).toBe(5);
    expect(s.caps.maxToolCalls).toBe(10);
    expect(s.caps.maxDurationSeconds).toBe(60);
  });

  it("resetRun clears tool count", () => {
    const tracker = new BudgetTracker(new Budget({ maxToolCalls: 3 }));
    tracker.recordToolCall();
    tracker.recordToolCall();
    tracker.resetRun();
    expect(tracker.toolCallCount).toBe(0);
    tracker.recordToolCall();
    expect(tracker.toolCallCount).toBe(1);
  });
});
