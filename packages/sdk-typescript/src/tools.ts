/**
 * Tool-call tracking, approval gates, and manual recording for MCP servers
 * and custom tools.
 *
 * Mirrors the Python `wickd.tools` module: lets users wrap functions with
 * `trackTool` or `toolApproval`, or call `recordToolCall` manually for
 * external integrations.
 */

import { getActiveTrace, recordToolCallOnTracker } from "./interceptor.js";
import {
  approvalGate,
  ApprovalDenied,
  type ApprovalHandler,
  terminalApprovalHandler,
} from "./approvals.js";
import { BudgetExceeded } from "./budget.js";

const MAX_PREVIEW = 200;

function buildInputPreview(args: unknown[]): string {
  try {
    return args.length > 0 ? JSON.stringify(args).slice(0, MAX_PREVIEW) : "";
  } catch {
    return String(args).slice(0, MAX_PREVIEW);
  }
}

function buildOutputPreview(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value.slice(0, MAX_PREVIEW);
  // Errors don't have enumerable properties, so JSON.stringify would return "{}".
  if (value instanceof Error) return value.message.slice(0, MAX_PREVIEW);
  try {
    return JSON.stringify(value).slice(0, MAX_PREVIEW);
  } catch {
    return String(value).slice(0, MAX_PREVIEW);
  }
}

function traceTool(
  toolName: string,
  server: string | undefined,
  latencyMs: number,
  inputPreview: string,
  output: unknown,
  status: "success" | "error" | "denied",
): void {
  const trace = getActiveTrace();
  if (trace) {
    trace.addToolCall({
      toolName,
      latencyMs: Math.round(latencyMs * 10) / 10,
      toolInput: inputPreview,
      toolOutput: buildOutputPreview(output),
      toolStatus: status,
      toolServer: server,
    });
  }
  // Count actual invocations (success/error) toward the runaway guard.
  // Denials are intentional stops and do not count.
  if (status !== "denied") {
    recordToolCallOnTracker();
  }
}

export interface TrackToolOptions {
  name?: string;
  server?: string;
}

/** Wrap a function so that every invocation is recorded in the active trace. */
export function trackTool<TArgs extends unknown[], TReturn>(
  fn: (...args: TArgs) => TReturn | Promise<TReturn>,
  options: TrackToolOptions = {},
): (...args: TArgs) => Promise<TReturn> {
  const toolName = options.name ?? fn.name ?? "tool";
  const server = options.server;

  return async (...args: TArgs): Promise<TReturn> => {
    const preview = buildInputPreview(args);
    const start = performance.now();
    let result: TReturn;
    try {
      result = await fn(...args);
    } catch (err) {
      // Propagate runaway kills without re-tracing as tool error.
      if (err instanceof BudgetExceeded) throw err;
      traceTool(toolName, server, performance.now() - start, preview, err, "error");
      throw err;
    }
    traceTool(toolName, server, performance.now() - start, preview, result, "success");
    return result;
  };
}

export interface ToolApprovalOptions extends TrackToolOptions {
  handler?: ApprovalHandler;
}

/** Gate a tool behind human approval, then track it in the active trace. */
export function toolApproval<TArgs extends unknown[], TReturn>(
  fn: (...args: TArgs) => TReturn | Promise<TReturn>,
  options: ToolApprovalOptions = {},
): (...args: TArgs) => Promise<TReturn> {
  const toolName = options.name ?? fn.name ?? "tool";
  const server = options.server;
  const handler = options.handler ?? terminalApprovalHandler;

  const tracked = trackTool(fn, { name: toolName, server });
  const gated = approvalGate(toolName, tracked, handler);

  return async (...args: TArgs): Promise<TReturn> => {
    try {
      return await gated(...args);
    } catch (err) {
      if (err instanceof ApprovalDenied) {
        traceTool(toolName, server, 0, buildInputPreview(args), "Denied by approver", "denied");
      }
      throw err;
    }
  };
}

export interface RecordToolCallOptions {
  toolName: string;
  toolInput?: unknown;
  toolOutput?: unknown;
  latencyMs?: number;
  toolStatus?: "success" | "error" | "denied";
  server?: string;
}

/** Manually record a tool call in the active trace. */
export function recordToolCall(options: RecordToolCallOptions): void {
  const trace = getActiveTrace();
  if (!trace) return;
  const status = options.toolStatus ?? "success";
  trace.addToolCall({
    toolName: options.toolName,
    latencyMs: options.latencyMs != null ? Math.round(options.latencyMs * 10) / 10 : 0,
    toolInput: buildOutputPreview(options.toolInput),
    toolOutput: buildOutputPreview(options.toolOutput),
    toolStatus: status,
    toolServer: options.server,
  });
  if (status !== "denied") {
    recordToolCallOnTracker();
  }
}
