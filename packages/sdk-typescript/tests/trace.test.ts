import { describe, it, expect } from "vitest";
import { Trace, TraceEvent, TraceStore } from "../src/trace.js";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

describe("TraceEvent", () => {
  it("strips null values in toDict", () => {
    const e = new TraceEvent("llm_call", { model: "gpt-4o", cost: 0.01 });
    const d = e.toDict();
    expect(d.model).toBe("gpt-4o");
    expect(d.approvalName).toBeUndefined();
  });
});

describe("Trace", () => {
  it("tracks LLM calls", () => {
    const t = new Trace("test");
    t.addLlmCall({
      model: "gpt-4o", provider: "openai",
      inputTokens: 500, outputTokens: 200,
      cost: 0.0045, latencyMs: 150,
    });
    expect(t.events).toHaveLength(1);
    expect(t.totalCost).toBe(0.0045);
    expect(t.totalInputTokens).toBe(500);
  });

  it("tracks cumulative cost", () => {
    const t = new Trace("test");
    t.addLlmCall({ model: "gpt-4o", provider: "openai", inputTokens: 100, outputTokens: 50, cost: 0.01, latencyMs: 100 });
    t.addLlmCall({ model: "gpt-4o", provider: "openai", inputTokens: 100, outputTokens: 50, cost: 0.02, latencyMs: 100 });
    expect(t.totalCost).toBeCloseTo(0.03);
  });

  it("records approvals", () => {
    const t = new Trace("test");
    t.addApproval("db_write", "approved");
    expect(t.events[0].approvalName).toBe("db_write");
  });

  it("records budget kill", () => {
    const t = new Trace("test");
    t.addBudgetKill("perRun", 2.5);
    expect(t.status).toBe("budget_killed");
  });

  it("completes", () => {
    const t = new Trace("test");
    t.complete();
    expect(t.status).toBe("completed");
    expect(t.endedAt).toBeDefined();
    expect(t.durationMs()).toBeGreaterThanOrEqual(0);
  });

  it("preserves killed status on complete", () => {
    const t = new Trace("test");
    t.addBudgetKill("perRun", 1);
    t.complete();
    expect(t.status).toBe("budget_killed");
  });

  it("serializes to dict", () => {
    const t = new Trace("test", "hello");
    t.addLlmCall({ model: "gpt-4o", provider: "openai", inputTokens: 100, outputTokens: 50, cost: 0.01, latencyMs: 100 });
    t.complete({
      runSpend: 0.01,
      dailySpend: 0.01,
      monthlySpend: 0.01,
      callCount: 1,
      remaining: null,
      killed: false,
      caps: {},
    });
    const d = t.toDict();
    expect(d.agentName).toBe("test");
    expect(d.llmCalls).toBe(1);
    expect(d.status).toBe("completed");
  });
});

describe("TraceStore", () => {
  it("saves and lists", () => {
    const dir = mkdtempSync(join(tmpdir(), "wickd-test-"));
    const store = new TraceStore(dir);
    const t = new Trace("test_agent", "test task");
    t.complete();
    store.save(t);

    const traces = store.listTraces();
    expect(traces).toHaveLength(1);
    expect(traces[0].agentName).toBe("test_agent");
  });

  it("gets by id", () => {
    const dir = mkdtempSync(join(tmpdir(), "wickd-test-"));
    const store = new TraceStore(dir);
    const t = new Trace("test_agent");
    t.complete();
    store.save(t);

    const loaded = store.getTrace(t.traceId.slice(0, 8));
    expect(loaded).not.toBeNull();
    expect(loaded!.traceId).toBe(t.traceId);
  });

  it("returns null for missing trace", () => {
    const dir = mkdtempSync(join(tmpdir(), "wickd-test-"));
    const store = new TraceStore(dir);
    expect(store.getTrace("nonexistent")).toBeNull();
  });
});
