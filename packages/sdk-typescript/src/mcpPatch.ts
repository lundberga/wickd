/**
 * Auto-patch Model Context Protocol (MCP) client sessions.
 *
 * Patches `Client.callTool` from `@modelcontextprotocol/sdk` so every MCP
 * tool call inside an agent run is recorded in the active trace and, when
 * the tool name is in the agent's approval allowlist, gated behind a
 * human-approval handler.
 *
 * Graceful no-op when the MCP SDK is not installed.
 */

import { AsyncLocalStorage } from "node:async_hooks";
import { getActiveTrace, recordToolCallOnTracker } from "./interceptor.js";
import { ApprovalDenied, terminalApprovalHandler, type ApprovalHandler } from "./approvals.js";
import { BudgetExceeded } from "./budget.js";

const SENTINEL = "_wickdMcpPatched";
const MAX_PREVIEW = 200;

interface ApprovalConfig {
  required: ReadonlySet<string>;
  handler: ApprovalHandler | null;
}

const approvalStorage = new AsyncLocalStorage<ApprovalConfig>();

const patchState = {
  installed: false,
  patched: false,
  verified: false,
  error: null as string | null,
};

export interface McpPatchStatus {
  installed: boolean;
  patched: boolean;
  verified: boolean;
  error: string | null;
}

/** Run `fn` with the given MCP approval config in scope for the current run. */
export function runWithMcpApprovalConfig<T>(
  config: { required?: string[]; handler?: ApprovalHandler | null },
  fn: () => T,
): T {
  const scoped: ApprovalConfig = {
    required: new Set(config.required ?? []),
    handler: config.handler ?? null,
  };
  return approvalStorage.run(scoped, fn);
}

function getApprovalConfig(): ApprovalConfig | null {
  return approvalStorage.getStore() ?? null;
}

function previewValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  try {
    return typeof value === "string"
      ? value.slice(0, MAX_PREVIEW)
      : JSON.stringify(value).slice(0, MAX_PREVIEW);
  } catch {
    return String(value).slice(0, MAX_PREVIEW);
  }
}

function previewResult(result: unknown): string {
  if (result === undefined || result === null) return "";
  const content = (result as { content?: unknown })?.content;
  if (Array.isArray(content)) {
    try {
      const parts = content.map((item: unknown) => {
        const text = (item as { text?: unknown })?.text;
        return text !== undefined && text !== null ? String(text) : JSON.stringify(item);
      });
      return parts.join(" ").slice(0, MAX_PREVIEW);
    } catch {
      // fall through
    }
  }
  return previewValue(result);
}

function isErrorResult(result: unknown): boolean {
  return Boolean((result as { isError?: unknown })?.isError);
}

function extractToolName(params: unknown): string {
  const p = params as Record<string, unknown> | null | undefined;
  const name = p?.name;
  return typeof name === "string" ? name : "unknown";
}

function extractArguments(params: unknown): unknown {
  const p = params as Record<string, unknown> | null | undefined;
  return p?.arguments;
}

function extractServerName(session: unknown): string | undefined {
  const s = session as Record<string, unknown> | null | undefined;
  if (!s) return undefined;
  const serverInfo = s.serverInfo ?? s._serverInfo;
  if (serverInfo && typeof serverInfo === "object") {
    const name = (serverInfo as { name?: unknown }).name;
    if (typeof name === "string") return name;
  }
  const override = s._wickdServerName;
  if (typeof override === "string") return override;
  return undefined;
}

async function runApproval(
  toolName: string,
  args: unknown,
  server: string | undefined,
): Promise<boolean> {
  const config = getApprovalConfig();
  const handler = config?.handler ?? terminalApprovalHandler;
  const trace = getActiveTrace();
  const context = {
    name: toolName,
    function: server ? `mcp:${server}:${toolName}` : `mcp:${toolName}`,
    argsPreview: previewValue(args),
    traceId: trace?.traceId.slice(0, 8) ?? "unknown",
  };
  return Boolean(await handler(context));
}

type CallToolFn = (this: unknown, ...args: unknown[]) => Promise<unknown>;

function loadMcpClientCtor(): (new (...args: unknown[]) => unknown) | null {
  for (const path of [
    "@modelcontextprotocol/sdk/client/index.js",
    "@modelcontextprotocol/sdk/client/index.cjs",
    "@modelcontextprotocol/sdk",
  ]) {
    try {
      const mod = require(path) as Record<string, unknown>;
      const ctor = mod.Client ?? (mod.default as { Client?: unknown })?.Client;
      if (typeof ctor === "function") {
        return ctor as new (...args: unknown[]) => unknown;
      }
    } catch {
      // try next
    }
  }
  return null;
}

function wrapCallTool(original: CallToolFn): CallToolFn {
  async function wrapped(this: unknown, ...args: unknown[]): Promise<unknown> {
    const params = args[0];
    const toolName = extractToolName(params);
    const toolArgs = extractArguments(params);
    const server = extractServerName(this);
    const trace = getActiveTrace();
    const config = getApprovalConfig();

    if (config?.required.has(toolName)) {
      trace?.addApproval(`mcp:${toolName}`, "pending");
      const approved = await runApproval(toolName, toolArgs, server);
      if (!approved) {
        trace?.addApproval(`mcp:${toolName}`, "denied");
        trace?.addToolCall({
          toolName,
          latencyMs: 0,
          toolInput: previewValue(toolArgs),
          toolOutput: "Denied by approver",
          toolStatus: "denied",
          toolServer: server,
        });
        throw new ApprovalDenied(toolName);
      }
      trace?.addApproval(`mcp:${toolName}`, "approved");
    }

    const start = performance.now();
    let result: unknown;
    try {
      result = await original.apply(this, args);
    } catch (err) {
      if (err instanceof BudgetExceeded) throw err;
      trace?.addToolCall({
        toolName,
        latencyMs: performance.now() - start,
        toolInput: previewValue(toolArgs),
        toolOutput: err instanceof Error ? err.message.slice(0, MAX_PREVIEW) : String(err).slice(0, MAX_PREVIEW),
        toolStatus: "error",
        toolServer: server,
      });
      try {
        recordToolCallOnTracker();
      } catch (cap) {
        if (!(cap instanceof BudgetExceeded)) throw cap;
        // Prefer the user's original exception over the runaway signal here.
      }
      throw err;
    }

    const status: "success" | "error" = isErrorResult(result) ? "error" : "success";
    trace?.addToolCall({
      toolName,
      latencyMs: performance.now() - start,
      toolInput: previewValue(toolArgs),
      toolOutput: previewResult(result),
      toolStatus: status,
      toolServer: server,
    });
    // May throw BudgetExceeded if max_tool_calls / max_duration trips.
    recordToolCallOnTracker();
    return result;
  }

  (wrapped as unknown as Record<string, unknown>)[SENTINEL] = true;
  return wrapped;
}

/** Patch `Client.callTool` on the MCP SDK. Idempotent; no-op if SDK missing. */
export function patchMcp(): boolean {
  const ctor = loadMcpClientCtor();
  if (!ctor) {
    patchState.installed = false;
    patchState.patched = false;
    patchState.verified = false;
    return false;
  }
  patchState.installed = true;

  const proto = ctor.prototype as Record<string, unknown>;
  const original = proto.callTool as CallToolFn | undefined;
  if (typeof original !== "function") {
    patchState.patched = false;
    patchState.verified = false;
    patchState.error = "Client.callTool not found";
    return false;
  }

  if ((original as unknown as Record<string, unknown>)[SENTINEL]) {
    patchState.patched = true;
    patchState.verified = true;
    return true;
  }

  try {
    (proto as Record<string, unknown>)._wickdOriginalCallTool = original;
    proto.callTool = wrapCallTool(original);
    patchState.patched = true;
    patchState.verified = true;
    patchState.error = null;
    return true;
  } catch (err) {
    patchState.patched = false;
    patchState.verified = false;
    patchState.error = err instanceof Error ? err.message : String(err);
    return false;
  }
}

export function verifyMcpPatch(): boolean {
  const ctor = loadMcpClientCtor();
  if (!ctor) {
    patchState.verified = false;
    return false;
  }
  const proto = ctor.prototype as Record<string, unknown>;
  const current = proto.callTool as CallToolFn | undefined;
  const verified = Boolean(
    current && (current as unknown as Record<string, unknown>)[SENTINEL],
  );
  patchState.patched = verified;
  patchState.verified = verified;
  return verified;
}

export function unpatchMcp(): boolean {
  const ctor = loadMcpClientCtor();
  if (!ctor) return false;
  const proto = ctor.prototype as Record<string, unknown>;
  const original = proto._wickdOriginalCallTool as CallToolFn | undefined;
  if (!original) return false;
  proto.callTool = original;
  delete proto._wickdOriginalCallTool;
  patchState.patched = false;
  patchState.verified = false;
  return true;
}

export function mcpPatchStatus(): McpPatchStatus {
  return { ...patchState };
}

/**
 * Test-only: patch an arbitrary Client-like constructor, bypassing
 * `@modelcontextprotocol/sdk` module resolution. Not exported from the
 * package index; for internal tests only.
 */
export function _patchClientCtorForTests(
  ctor: { prototype: Record<string, unknown> },
): boolean {
  patchState.installed = true;
  const proto = ctor.prototype;
  const original = proto.callTool as CallToolFn | undefined;
  if (typeof original !== "function") {
    patchState.error = "Client.callTool not found";
    return false;
  }
  if ((original as unknown as Record<string, unknown>)[SENTINEL]) {
    patchState.patched = true;
    patchState.verified = true;
    return true;
  }
  proto._wickdOriginalCallTool = original;
  proto.callTool = wrapCallTool(original);
  patchState.patched = true;
  patchState.verified = true;
  return true;
}

export function _unpatchClientCtorForTests(
  ctor: { prototype: Record<string, unknown> },
): boolean {
  const proto = ctor.prototype;
  const original = proto._wickdOriginalCallTool as CallToolFn | undefined;
  if (!original) return false;
  proto.callTool = original;
  delete proto._wickdOriginalCallTool;
  patchState.patched = false;
  patchState.verified = false;
  return true;
}
