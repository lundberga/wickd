import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  _patchClientCtorForTests,
  _unpatchClientCtorForTests,
  runWithMcpApprovalConfig,
  mcpPatchStatus,
} from "../src/mcpPatch.js";
import { Trace } from "../src/trace.js";
import { Budget, BudgetTracker, BudgetExceeded } from "../src/budget.js";
import { runWithContext } from "../src/interceptor.js";
import { ApprovalDenied, autoApproveHandler, autoDenyHandler } from "../src/approvals.js";

// Minimal stand-in for @modelcontextprotocol/sdk Client.
class FakeClient {
  serverInfo?: { name: string };
  constructor(serverName?: string, private impl?: (name: string, args: unknown) => Promise<unknown>) {
    if (serverName) this.serverInfo = { name: serverName };
  }

  async callTool(params: { name: string; arguments?: unknown }): Promise<unknown> {
    if (this.impl) {
      return this.impl(params.name, params.arguments);
    }
    return {
      content: [{ type: "text", text: `ok:${params.name}` }],
      isError: false,
    };
  }
}

describe("patchMcp wrapping", () => {
  beforeEach(() => {
    _patchClientCtorForTests(FakeClient);
  });

  afterEach(() => {
    _unpatchClientCtorForTests(FakeClient);
  });

  it("records successful MCP tool call", async () => {
    const trace = new Trace("t", "t");
    const client = new FakeClient("docs-mcp");

    await runWithContext({ tracker: null, trace }, async () => {
      const result: any = await client.callTool({ name: "search", arguments: { q: "wickd" } });
      expect(result.content[0].text).toBe("ok:search");
    });

    expect(trace.events).toHaveLength(1);
    const event = trace.events[0];
    expect(event.eventType).toBe("tool_call");
    expect(event.toolName).toBe("search");
    expect(event.toolStatus).toBe("success");
    expect(event.toolServer).toBe("docs-mcp");
    expect(event.toolInputPreview).toContain("wickd");
    expect(event.toolOutputPreview).toContain("ok:search");
  });

  it("records errors without masking the original throw", async () => {
    const trace = new Trace("t", "t");
    const client = new FakeClient("srv", async () => {
      throw new Error("boom");
    });

    await runWithContext({ tracker: null, trace }, async () => {
      await expect(client.callTool({ name: "broken" })).rejects.toThrow("boom");
    });
    expect(trace.events[0].toolStatus).toBe("error");
    expect(trace.events[0].toolOutputPreview).toContain("boom");
  });

  it("isError result flips status to error", async () => {
    const trace = new Trace("t", "t");
    const client = new FakeClient("srv", async () => ({
      content: [{ type: "text", text: "permission denied" }],
      isError: true,
    }));

    await runWithContext({ tracker: null, trace }, async () => {
      await client.callTool({ name: "read_file", arguments: { path: "/etc/passwd" } });
    });

    expect(trace.events[0].toolStatus).toBe("error");
  });

  it("works without an active trace", async () => {
    const client = new FakeClient("srv");
    const result: any = await client.callTool({ name: "ping" });
    expect(result.content[0].text).toBe("ok:ping");
  });

  it("is idempotent (double-patch does not double-wrap)", () => {
    // Already patched by beforeEach; patching again should return true
    // without wrapping the wrapper.
    const before = FakeClient.prototype.callTool;
    _patchClientCtorForTests(FakeClient);
    expect(FakeClient.prototype.callTool).toBe(before);
  });

  it("unpatch restores original", async () => {
    _unpatchClientCtorForTests(FakeClient);
    expect(mcpPatchStatus().patched).toBe(false);

    // Call now should not touch the trace.
    const trace = new Trace("t", "t");
    const client = new FakeClient("srv");
    await runWithContext({ tracker: null, trace }, async () => {
      await client.callTool({ name: "noop" });
    });
    expect(trace.events).toHaveLength(0);

    // Re-patch so afterEach teardown succeeds (it will unpatch again safely).
    _patchClientCtorForTests(FakeClient);
  });
});

describe("MCP approval gating", () => {
  beforeEach(() => {
    _patchClientCtorForTests(FakeClient);
  });

  afterEach(() => {
    _unpatchClientCtorForTests(FakeClient);
  });

  it("approves listed tool via handler", async () => {
    const trace = new Trace("t", "t");
    const client = new FakeClient("postgres-mcp");

    await runWithContext({ tracker: null, trace }, async () => {
      await runWithMcpApprovalConfig(
        { required: ["drop_table"], handler: autoApproveHandler },
        async () => {
          const result: any = await client.callTool({
            name: "drop_table",
            arguments: { table: "stale_cache" },
          });
          expect(result.content[0].text).toBe("ok:drop_table");
        },
      );
    });

    const types = trace.events.map(e => e.eventType);
    expect(types.filter(t => t === "approval_gate")).toHaveLength(2); // pending + approved
    const toolEvent = trace.events.find(e => e.eventType === "tool_call");
    expect(toolEvent?.toolStatus).toBe("success");
  });

  it("denies listed tool and records denial", async () => {
    const trace = new Trace("t", "t");
    const client = new FakeClient("postgres-mcp");

    await runWithContext({ tracker: null, trace }, async () => {
      await runWithMcpApprovalConfig(
        { required: ["drop_table"], handler: autoDenyHandler },
        async () => {
          await expect(
            client.callTool({ name: "drop_table", arguments: {} }),
          ).rejects.toThrow(ApprovalDenied);
        },
      );
    });

    const toolEvents = trace.events.filter(e => e.eventType === "tool_call");
    expect(toolEvents).toHaveLength(1);
    expect(toolEvents[0].toolStatus).toBe("denied");
  });

  it("non-listed tool bypasses gate", async () => {
    const trace = new Trace("t", "t");
    const client = new FakeClient("srv");

    await runWithContext({ tracker: null, trace }, async () => {
      await runWithMcpApprovalConfig(
        { required: ["drop_table"], handler: autoDenyHandler },
        async () => {
          // read_file is not in the allowlist; should pass through.
          const result: any = await client.callTool({
            name: "read_file",
            arguments: { path: "/tmp/x" },
          });
          expect(result.content[0].text).toBe("ok:read_file");
        },
      );
    });

    const approvals = trace.events.filter(e => e.eventType === "approval_gate");
    expect(approvals).toHaveLength(0);
  });
});

describe("MCP + runaway guards", () => {
  beforeEach(() => {
    _patchClientCtorForTests(FakeClient);
  });

  afterEach(() => {
    _unpatchClientCtorForTests(FakeClient);
  });

  it("MCP tool calls count toward maxToolCalls", async () => {
    // maxToolCalls=3: the 3rd invocation runs its side effect, increments
    // the counter to 3, then the guard (>= 3) fires and throws — matching
    // Python semantics.
    const tracker = new BudgetTracker(new Budget({ maxToolCalls: 3 }));
    const trace = new Trace("t", "t");
    const client = new FakeClient("srv");

    await runWithContext({ tracker, trace }, async () => {
      await client.callTool({ name: "step1" });
      await client.callTool({ name: "step2" });
      await expect(client.callTool({ name: "step3" })).rejects.toThrow(BudgetExceeded);
    });
    expect(tracker.toolCallCount).toBe(3);
  });
});
