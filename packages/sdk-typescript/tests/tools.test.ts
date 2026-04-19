import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { Trace } from "../src/trace.js";
import { Budget, BudgetTracker, BudgetExceeded } from "../src/budget.js";
import { trackTool, toolApproval, recordToolCall } from "../src/tools.js";
import { runWithContext } from "../src/interceptor.js";
import { ApprovalDenied, autoApproveHandler, autoDenyHandler } from "../src/approvals.js";

describe("trackTool", () => {
  it("records successful tool call in trace", async () => {
    const trace = new Trace("t", "t");
    await runWithContext({ tracker: null, trace }, async () => {
      const search = trackTool(async (q: string) => `found ${q}`, { name: "search" });
      const result = await search("hello");
      expect(result).toBe("found hello");
    });
    expect(trace.events).toHaveLength(1);
    const event = trace.events[0];
    expect(event.eventType).toBe("tool_call");
    expect(event.toolName).toBe("search");
    expect(event.toolStatus).toBe("success");
    expect(event.toolInputPreview).toContain("hello");
    expect(event.toolOutputPreview).toContain("found hello");
  });

  it("records error and re-throws", async () => {
    const trace = new Trace("t", "t");
    const failing = trackTool(async () => {
      throw new Error("boom");
    }, { name: "failing" });

    await runWithContext({ tracker: null, trace }, async () => {
      await expect(failing()).rejects.toThrow("boom");
    });

    expect(trace.events[0].toolStatus).toBe("error");
    expect(trace.events[0].toolOutputPreview).toContain("boom");
  });

  it("increments tracker tool-call count", async () => {
    const tracker = new BudgetTracker(new Budget({ maxToolCalls: 2 }));
    const trace = new Trace("t", "t");
    const step = trackTool(async () => "ok", { name: "step" });

    await runWithContext({ tracker, trace }, async () => {
      await step();
      expect(tracker.toolCallCount).toBe(1);
      await expect(step()).rejects.toThrow(BudgetExceeded);
    });
  });

  it("honors server name", async () => {
    const trace = new Trace("t", "t");
    const query = trackTool(async (_sql: string) => "ok", {
      name: "query",
      server: "postgres-mcp",
    });
    await runWithContext({ tracker: null, trace }, async () => {
      await query("SELECT 1");
    });
    expect(trace.events[0].toolServer).toBe("postgres-mcp");
  });
});

describe("toolApproval", () => {
  it("approved call proceeds and records success", async () => {
    const trace = new Trace("t", "t");
    const dangerous = toolApproval(async () => "did it", {
      name: "delete_user",
      handler: autoApproveHandler,
    });

    await runWithContext({ tracker: null, trace }, async () => {
      const result = await dangerous();
      expect(result).toBe("did it");
    });

    const types = trace.events.map(e => e.eventType);
    expect(types).toContain("approval_gate");
    expect(types).toContain("tool_call");
    const toolEvent = trace.events.find(e => e.eventType === "tool_call");
    expect(toolEvent?.toolStatus).toBe("success");
  });

  it("denied call throws and records denial", async () => {
    const trace = new Trace("t", "t");
    const dangerous = toolApproval(async () => "should not run", {
      name: "drop_table",
      handler: autoDenyHandler,
    });

    await runWithContext({ tracker: null, trace }, async () => {
      await expect(dangerous()).rejects.toThrow(ApprovalDenied);
    });

    const toolEvents = trace.events.filter(e => e.eventType === "tool_call");
    expect(toolEvents.some(e => e.toolStatus === "denied")).toBe(true);
  });

  it("denied call does not count toward runaway guard", async () => {
    const tracker = new BudgetTracker(new Budget({ maxToolCalls: 1 }));
    const trace = new Trace("t", "t");
    const dangerous = toolApproval(async () => "ok", {
      name: "drop_table",
      handler: autoDenyHandler,
    });

    await runWithContext({ tracker, trace }, async () => {
      await expect(dangerous()).rejects.toThrow(ApprovalDenied);
    });
    expect(tracker.toolCallCount).toBe(0);
  });
});

describe("recordToolCall", () => {
  it("records manually in the trace", async () => {
    const trace = new Trace("t", "t");
    await runWithContext({ tracker: null, trace }, () => {
      recordToolCall({
        toolName: "external_api",
        toolInput: { endpoint: "/users" },
        toolOutput: { users: [1, 2, 3] },
        latencyMs: 250.5,
        server: "rest-api",
      });
    });
    expect(trace.events).toHaveLength(1);
    const event = trace.events[0];
    expect(event.toolName).toBe("external_api");
    expect(event.toolServer).toBe("rest-api");
    expect(event.latencyMs).toBe(250.5);
  });

  it("does not crash without an active trace", () => {
    expect(() =>
      recordToolCall({ toolName: "x", toolInput: "in", toolOutput: "out" }),
    ).not.toThrow();
  });
});
