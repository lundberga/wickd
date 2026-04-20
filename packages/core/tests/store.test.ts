import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  InvalidStateError,
  RunNotFoundError,
  SpanNotFoundError,
  Store,
  createCatalog,
} from "../src/index.js";

describe("Store.startRun / endRun", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("creates a run in 'running' state", () => {
    const run = store.startRun({ name: "agent-a" });

    expect(run.name).toBe("agent-a");
    expect(run.status).toBe("running");
    expect(run.endedAt).toBeNull();
    expect(run.totalCostUsd).toBe(0);
    expect(run.metadata).toEqual({});
  });

  it("persists metadata as a JSON-safe object", () => {
    const run = store.startRun({
      name: "agent-a",
      metadata: { version: "1.2.3", tags: ["prod"] },
    });
    const reloaded = store.getRun(run.id);
    expect(reloaded?.metadata).toEqual({ version: "1.2.3", tags: ["prod"] });
  });

  it("transitions a running run to completed", () => {
    const run = store.startRun({ name: "agent-a" });
    const ended = store.endRun(run.id, { status: "completed" });

    expect(ended.status).toBe("completed");
    expect(ended.endedAt).not.toBeNull();
    expect(ended.error).toBeNull();
  });

  it("records the error when a run fails", () => {
    const run = store.startRun({ name: "agent-a" });
    const ended = store.endRun(run.id, {
      status: "failed",
      error: "boom",
    });
    expect(ended.status).toBe("failed");
    expect(ended.error).toBe("boom");
  });

  it("throws RunNotFoundError when ending an unknown run", () => {
    expect(() =>
      store.endRun("nope", { status: "completed" }),
    ).toThrow(RunNotFoundError);
  });

  it("throws InvalidStateError when ending an already-ended run", () => {
    const run = store.startRun({ name: "agent-a" });
    store.endRun(run.id, { status: "completed" });
    expect(() => store.endRun(run.id, { status: "completed" })).toThrow(
      InvalidStateError,
    );
  });

  it("returns null from getRun for unknown ids", () => {
    expect(store.getRun("ghost")).toBeNull();
  });
});

describe("Store.listRuns", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("returns runs ordered by most-recent first", async () => {
    const a = store.startRun({ name: "a" });
    await sleepOneMs();
    const b = store.startRun({ name: "b" });
    await sleepOneMs();
    const c = store.startRun({ name: "c" });

    const runs = store.listRuns();
    expect(runs.map((r) => r.id)).toEqual([c.id, b.id, a.id]);
  });

  it("filters by status", () => {
    const a = store.startRun({ name: "a" });
    const b = store.startRun({ name: "b" });
    store.endRun(b.id, { status: "completed" });

    const running = store.listRuns({ status: "running" });
    expect(running.map((r) => r.id)).toEqual([a.id]);

    const completed = store.listRuns({ status: "completed" });
    expect(completed.map((r) => r.id)).toEqual([b.id]);
  });

  it("respects limit and offset", async () => {
    const ids: string[] = [];
    for (let i = 0; i < 5; i++) {
      ids.push(store.startRun({ name: `r${i}` }).id);
      await sleepOneMs();
    }
    const page1 = store.listRuns({ limit: 2, offset: 0 });
    const page2 = store.listRuns({ limit: 2, offset: 2 });

    expect(page1).toHaveLength(2);
    expect(page2).toHaveLength(2);
    // Pages should not overlap.
    expect(page1[0]?.id).not.toBe(page2[0]?.id);
  });

  it("clamps absurd limits and ignores bad values", () => {
    store.startRun({ name: "a" });
    expect(store.listRuns({ limit: -1 })).toHaveLength(1);
    expect(store.listRuns({ limit: Number.NaN })).toHaveLength(1);
    expect(store.listRuns({ limit: 1_000_000 })).toHaveLength(1);
  });
});

describe("Store.startSpan / endSpan", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("attaches spans to a run and retrieves them in order", async () => {
    const run = store.startRun({ name: "agent" });

    const s1 = store.startSpan({
      runId: run.id,
      kind: "llm",
      name: "gpt-4o",
      model: "gpt-4o",
      provider: "openai",
    });
    await sleepOneMs();
    const s2 = store.startSpan({
      runId: run.id,
      kind: "mcp_tool",
      name: "search",
    });

    const spans = store.getSpansForRun(run.id);
    expect(spans.map((s) => s.id)).toEqual([s1.id, s2.id]);
  });

  it("auto-calculates cost from model + token counts", () => {
    const run = store.startRun({ name: "agent" });
    const span = store.startSpan({
      runId: run.id,
      kind: "llm",
      name: "gpt-4o",
      model: "gpt-4o",
      provider: "openai",
    });

    const ended = store.endSpan(span.id, {
      status: "ok",
      tokensInput: 1000,
      tokensOutput: 500,
    });

    expect(ended.costUsd).toBe(0.0075);
    expect(ended.tokensInput).toBe(1000);
    expect(ended.tokensOutput).toBe(500);
  });

  it("uses an explicit costUsd when provided", () => {
    const run = store.startRun({ name: "agent" });
    const span = store.startSpan({
      runId: run.id,
      kind: "http",
      name: "POST /x",
    });
    const ended = store.endSpan(span.id, {
      status: "ok",
      costUsd: 0.5,
    });
    expect(ended.costUsd).toBe(0.5);
  });

  it("uses the injected pricing catalog when auto-calculating", () => {
    const catalog = createCatalog([
      {
        provider: "custom",
        model: "custom-model",
        inputPerMillion: 10,
        outputPerMillion: 20,
      },
    ]);
    const s = new Store(":memory:", { pricingCatalog: catalog });
    try {
      const run = s.startRun({ name: "agent" });
      const span = s.startSpan({
        runId: run.id,
        kind: "llm",
        name: "custom-model",
        model: "custom-model",
      });
      const ended = s.endSpan(span.id, {
        status: "ok",
        tokensInput: 1_000_000,
        tokensOutput: 1_000_000,
      });
      expect(ended.costUsd).toBe(30);
    } finally {
      s.close();
    }
  });

  it("bumps run.totalCostUsd after each span", () => {
    const run = store.startRun({ name: "agent" });

    const s1 = store.startSpan({
      runId: run.id,
      kind: "llm",
      name: "gpt-4o",
      model: "gpt-4o",
    });
    store.endSpan(s1.id, {
      status: "ok",
      tokensInput: 1_000_000,
      tokensOutput: 0,
    });

    const s2 = store.startSpan({
      runId: run.id,
      kind: "llm",
      name: "gpt-4o",
      model: "gpt-4o",
    });
    store.endSpan(s2.id, {
      status: "ok",
      tokensInput: 0,
      tokensOutput: 1_000_000,
    });

    const reloaded = store.getRun(run.id);
    expect(reloaded?.totalCostUsd).toBeCloseTo(12.5, 6);
  });

  it("records error state when a span fails", () => {
    const run = store.startRun({ name: "agent" });
    const span = store.startSpan({
      runId: run.id,
      kind: "http",
      name: "GET /x",
    });
    const ended = store.endSpan(span.id, {
      status: "error",
      error: "500 Internal Server Error",
    });
    expect(ended.status).toBe("error");
    expect(ended.error).toBe("500 Internal Server Error");
  });

  it("throws SpanNotFoundError when ending an unknown span", () => {
    expect(() => store.endSpan("ghost", { status: "ok" })).toThrow(
      SpanNotFoundError,
    );
  });

  it("throws InvalidStateError when ending an already-ended span", () => {
    const run = store.startRun({ name: "agent" });
    const span = store.startSpan({
      runId: run.id,
      kind: "http",
      name: "n",
    });
    store.endSpan(span.id, { status: "ok" });
    expect(() => store.endSpan(span.id, { status: "ok" })).toThrow(
      InvalidStateError,
    );
  });

  it("persists structured input/output payloads", () => {
    const run = store.startRun({ name: "agent" });
    const span = store.startSpan({
      runId: run.id,
      kind: "mcp_tool",
      name: "search",
      input: { query: "wickd", limit: 5 },
    });
    const ended = store.endSpan(span.id, {
      status: "ok",
      output: { results: [{ title: "hit" }] },
    });
    expect(ended.input).toEqual({ query: "wickd", limit: 5 });
    expect(ended.output).toEqual({ results: [{ title: "hit" }] });
  });

  it("supports parent/child span relationships", () => {
    const run = store.startRun({ name: "agent" });
    const parent = store.startSpan({
      runId: run.id,
      kind: "mcp_tool",
      name: "tool",
    });
    const child = store.startSpan({
      runId: run.id,
      parentSpanId: parent.id,
      kind: "llm",
      name: "gpt-4o",
    });
    expect(child.parentSpanId).toBe(parent.id);
  });
});

function sleepOneMs(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 2));
}
