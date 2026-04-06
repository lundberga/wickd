import { describe, it, expect, vi, afterEach } from "vitest";
import { Budget, BudgetTracker, BudgetExceeded } from "../src/budget.js";

// ── _kill() onKill error surfacing ────────────────────────────────────────

describe("BudgetTracker._kill onKill error surfacing", () => {
  afterEach(() => {
    delete process.env.WICKD_DEBUG;
  });

  it("does not mask BudgetExceeded when onKill throws", () => {
    const budget = new Budget({
      perRun: 0.001,
      onKill: () => { throw new Error("kill callback boom"); },
    });
    const tracker = new BudgetTracker(budget);
    expect(() => tracker.recordCost(0.01)).toThrow(BudgetExceeded);
  });

  it("writes to stderr when WICKD_DEBUG is set and onKill throws", () => {
    process.env.WICKD_DEBUG = "1";
    const writes: string[] = [];
    const orig = process.stderr.write.bind(process.stderr);
    vi.spyOn(process.stderr, "write").mockImplementation((chunk: unknown) => {
      writes.push(String(chunk));
      return true;
    });

    const budget = new Budget({
      perRun: 0.001,
      onKill: () => { throw new Error("kill callback boom"); },
    });
    const tracker = new BudgetTracker(budget);
    try { tracker.recordCost(0.01); } catch { /* expected */ }

    expect(writes.some(w => w.includes("kill callback boom"))).toBe(true);
    vi.restoreAllMocks();
  });

  it("always writes handler errors to stderr (not gated behind WICKD_DEBUG)", () => {
    delete process.env.WICKD_DEBUG;
    const writes: string[] = [];
    vi.spyOn(process.stderr, "write").mockImplementation((chunk: unknown) => {
      writes.push(String(chunk));
      return true;
    });

    const budget = new Budget({
      perRun: 0.001,
      onKill: () => { throw new Error("kill callback boom"); },
    });
    const tracker = new BudgetTracker(budget);
    try { tracker.recordCost(0.01); } catch { /* expected */ }

    expect(writes.some(w => w.includes("kill callback boom"))).toBe(true);
    vi.restoreAllMocks();
  });
});

// ── interceptor patch status ─────────────────────────────────────────

describe("patchStatus", () => {
  it("reports all three providers with error field", async () => {
    const interceptorMod = await import("../src/interceptor.js");
    const status = interceptorMod.patchStatus();

    expect(status).toHaveProperty("openai");
    expect(status).toHaveProperty("anthropic");
    expect(status).toHaveProperty("google");

    // Providers not installed in test env — error should be null
    for (const info of Object.values(status)) {
      expect(info).toHaveProperty("error");
      expect(info.error).toBeNull();
    }
  });
});
