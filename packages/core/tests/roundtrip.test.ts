import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { Store, calculateCost } from "../src/index.js";

describe("roundtrip (file-backed)", () => {
  let dir: string;
  let dbPath: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "wickd-core-"));
    dbPath = join(dir, "wickd.db");
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("survives a close/reopen with full state intact", () => {
    const writer = new Store(dbPath);
    const run = writer.startRun({ name: "agent", metadata: { run: 1 } });
    const span = writer.startSpan({
      runId: run.id,
      kind: "llm",
      name: "gpt-4o",
      model: "gpt-4o",
    });
    writer.endSpan(span.id, {
      status: "ok",
      tokensInput: 10_000,
      tokensOutput: 5_000,
    });
    writer.endRun(run.id, { status: "completed" });
    writer.close();

    const reader = new Store(dbPath);
    try {
      const reloaded = reader.getRun(run.id);
      expect(reloaded?.status).toBe("completed");
      expect(reloaded?.metadata).toEqual({ run: 1 });
      expect(reloaded?.totalCostUsd).toBe(
        calculateCost("gpt-4o", 10_000, 5_000),
      );

      const spans = reader.getSpansForRun(run.id);
      expect(spans).toHaveLength(1);
      expect(spans[0]?.status).toBe("ok");
    } finally {
      reader.close();
    }
  });

  it("supports a read-only secondary connection on the same file", () => {
    const writer = new Store(dbPath);
    const run = writer.startRun({ name: "agent" });
    writer.endRun(run.id, { status: "completed" });

    const reader = new Store(dbPath, { readonly: true });
    try {
      const reloaded = reader.getRun(run.id);
      expect(reloaded?.status).toBe("completed");
    } finally {
      reader.close();
      writer.close();
    }
  });

  it("represents a realistic agent run: LLM + MCP + cost rollup", () => {
    const store = new Store(dbPath);
    try {
      const run = store.startRun({ name: "research-agent" });

      // LLM decides to call a tool
      const llm1 = store.startSpan({
        runId: run.id,
        kind: "llm",
        name: "gpt-4o",
        model: "gpt-4o",
        provider: "openai",
      });
      store.endSpan(llm1.id, {
        status: "ok",
        tokensInput: 500,
        tokensOutput: 50,
      });

      // MCP tool call (no direct cost here)
      const tool = store.startSpan({
        runId: run.id,
        kind: "mcp_tool",
        name: "search",
        input: { query: "wickd" },
      });
      store.endSpan(tool.id, {
        status: "ok",
        output: { hits: 3 },
      });

      // LLM reads tool output and answers
      const llm2 = store.startSpan({
        runId: run.id,
        kind: "llm",
        name: "gpt-4o",
        model: "gpt-4o",
        provider: "openai",
      });
      store.endSpan(llm2.id, {
        status: "ok",
        tokensInput: 800,
        tokensOutput: 200,
      });

      store.endRun(run.id, { status: "completed" });

      const reloaded = store.getRun(run.id);
      const expected =
        calculateCost("gpt-4o", 500, 50) + calculateCost("gpt-4o", 800, 200);
      expect(reloaded?.totalCostUsd).toBeCloseTo(expected, 6);

      const spans = store.getSpansForRun(run.id);
      expect(spans.map((s) => s.kind)).toEqual(["llm", "mcp_tool", "llm"]);
    } finally {
      store.close();
    }
  });
});
