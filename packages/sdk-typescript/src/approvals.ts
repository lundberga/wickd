import { createInterface } from "node:readline";
import { getActiveTrace } from "./interceptor.js";

export class ApprovalDenied extends Error {
  readonly actionName: string;
  readonly reason: string;

  constructor(name: string, reason = "") {
    super(`Approval denied for '${name}'. ${reason}`.trim());
    this.name = "ApprovalDenied";
    this.actionName = name;
    this.reason = reason;
  }
}

export interface ApprovalContext {
  name: string;
  function: string;
  argsPreview: string;
  traceId: string;
}

export type ApprovalHandler = (context: ApprovalContext) => boolean | Promise<boolean>;

/** Wrap a function with a human approval checkpoint. */
export function approvalGate<TArgs extends unknown[], TReturn>(
  name: string,
  fn: (...args: TArgs) => TReturn,
  handler: ApprovalHandler = terminalApprovalHandler,
): (...args: TArgs) => Promise<TReturn> {
  return async (...args: TArgs): Promise<TReturn> => {
    const trace = getActiveTrace();
    trace?.addApproval(name, "pending");

    const context: ApprovalContext = {
      name,
      function: fn.name || "anonymous",
      argsPreview: args.length > 0 ? JSON.stringify(args).slice(0, 200) : "",
      traceId: trace?.traceId.slice(0, 8) ?? "unknown",
    };

    process.stderr.write(`\x1b[33m[wickd] \u23F8 APPROVAL NEEDED: '${name}'\x1b[0m\n`);

    const approved = await handler(context);

    if (approved) {
      trace?.addApproval(name, "approved");
      process.stderr.write(`\x1b[32m[wickd] \u2713 Approved: '${name}'\x1b[0m\n`);
      return fn(...args);
    }

    trace?.addApproval(name, "denied");
    process.stderr.write(`\x1b[31m[wickd] \u2717 Denied: '${name}'\x1b[0m\n`);
    throw new ApprovalDenied(name);
  };
}

export const terminalApprovalHandler: ApprovalHandler = async (context) => {
  process.stderr.write(`\n${"=".repeat(50)}\n`);
  process.stderr.write(`  WICKD APPROVAL REQUEST\n`);
  process.stderr.write(`  Action: ${context.name}\n`);
  process.stderr.write(`  Function: ${context.function}\n`);
  if (context.argsPreview) process.stderr.write(`  Args: ${context.argsPreview}\n`);
  process.stderr.write(`${"=".repeat(50)}\n`);

  const rl = createInterface({ input: process.stdin, output: process.stderr });
  return new Promise<boolean>((resolve) => {
    rl.question("  Approve? [y/N]: ", (answer) => {
      rl.close();
      const normalized = answer.trim().toLowerCase();
      resolve(normalized === "y" || normalized === "yes");
    });
  });
};

export const autoApproveHandler: ApprovalHandler = () => true;

export const autoDenyHandler: ApprovalHandler = () => false;

/**
 * Webhook-based approval for remote/headless environments.
 * POSTs an approval request, then polls for a decision.
 */
export function webhookApprovalHandler(
  url: string,
  { pollInterval = 2000, timeout = 300_000, headers = {} }: {
    pollInterval?: number;
    timeout?: number;
    headers?: Record<string, string>;
  } = {},
): ApprovalHandler {
  const mergedHeaders: Record<string, string> = { "Content-Type": "application/json", ...headers };

  return async (context) => {
    let data: Record<string, unknown>;
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: mergedHeaders,
        body: JSON.stringify({
          type: "approval_request",
          name: context.name,
          function: context.function,
          argsPreview: context.argsPreview,
          traceId: context.traceId,
        }),
      });
      data = await resp.json() as Record<string, unknown>;
    } catch (e) {
      process.stderr.write(`\x1b[31m[wickd] Webhook approval failed: ${e}. Falling back to terminal.\x1b[0m\n`);
      return terminalApprovalHandler(context);
    }

    // Instant decision
    if ("approved" in data) return Boolean(data.approved);

    // Deferred -- poll for result
    const pollUrl = (data.poll_url ?? data.pollUrl) as string | undefined;
    if (!pollUrl) {
      process.stderr.write(`\x1b[31m[wickd] Webhook returned no decision or poll URL. Falling back to terminal.\x1b[0m\n`);
      return terminalApprovalHandler(context);
    }

    process.stderr.write(`\x1b[33m[wickd] Waiting for remote approval (${data.request_id ?? "?"})...\x1b[0m\n`);

    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, pollInterval));
      try {
        const pollResp = await fetch(pollUrl, { headers: mergedHeaders });
        const pollData = await pollResp.json() as Record<string, unknown>;
        if (pollData.status === "approved") return true;
        if (pollData.status === "denied") return false;
      } catch {
        // Transient network error during polling — retry on next iteration
      }
    }

    process.stderr.write(`\x1b[31m[wickd] Approval timed out. Denying.\x1b[0m\n`);
    return false;
  };
}
