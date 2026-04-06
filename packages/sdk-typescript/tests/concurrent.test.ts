import { describe, it, expect } from "vitest";
import { agent } from "../src/agent.js";
import { Budget, BudgetTracker } from "../src/budget.js";
import { getActiveTracker } from "../src/interceptor.js";

// Helper: create a delay
const delay = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

describe("concurrent run isolation", () => {
  it("two concurrent runs complete independently without state corruption", async () => {
    const results: string[] = [];

    const a = agent({
      autoPatch: false,
      fn: async (label: string) => {
        await delay(10);
        results.push(`start:${label}`);
        await delay(10);
        results.push(`end:${label}`);
        return `done:${label}`;
      },
    });

    const [r1, r2] = await Promise.all([a.run("A"), a.run("B")]);

    expect(r1).toBe("done:A");
    expect(r2).toBe("done:B");
    // Both runs completed
    expect(results).toContain("start:A");
    expect(results).toContain("start:B");
    expect(results).toContain("end:A");
    expect(results).toContain("end:B");
  });

  it("concurrent runs with budgets have isolated trackers", async () => {
    const seenTrackers: (BudgetTracker | null)[] = [];

    const a = agent({
      autoPatch: false,
      budget: new Budget({ perRun: 10 }),
      fn: async (label: string) => {
        seenTrackers.push(getActiveTracker());
        await delay(10); // ensure both runs overlap
        return label;
      },
    });

    const [r1, r2] = await Promise.all([a.run("X"), a.run("Y")]);

    expect(r1).toBe("X");
    expect(r2).toBe("Y");
    // Each run must have created a distinct BudgetTracker
    expect(seenTrackers).toHaveLength(2);
    expect(seenTrackers[0]).not.toBe(seenTrackers[1]);
    expect(seenTrackers[0]).toBeInstanceOf(BudgetTracker);
    expect(seenTrackers[1]).toBeInstanceOf(BudgetTracker);
  });

  it("sequential runs have clean state", async () => {
    let callCount = 0;

    const a = agent({
      autoPatch: false,
      fn: async () => {
        callCount++;
        return callCount;
      },
    });

    const r1 = await a.run();
    const r2 = await a.run();

    expect(r1).toBe(1);
    expect(r2).toBe(2);
  });

  it("many concurrent runs all resolve correctly", async () => {
    const a = agent({
      autoPatch: false,
      fn: async (n: number) => {
        await delay(n % 5); // deterministic stagger
        return n * 2;
      },
    });

    const inputs = Array.from({ length: 10 }, (_, i) => i);
    const results = await Promise.all(inputs.map(n => a.run(n)));

    expect(results).toEqual(inputs.map(n => n * 2));
  });
});
