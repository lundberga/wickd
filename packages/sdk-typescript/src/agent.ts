import { Budget, BudgetTracker, BudgetExceeded, WickdPatchError } from "./budget.js";
import { Trace, TraceStore, TraceEvent } from "./trace.js";
import { patchAll, verifyPatches, runWithContext } from "./interceptor.js";
import type { NotifyHandler } from "./notify.js";
import type { NotificationEvent, BudgetSummary } from "wickd-core";
import type { ApprovalHandler } from "./approvals.js";

export interface AgentOptions<TArgs extends unknown[], TReturn> {
  fn: (...args: TArgs) => TReturn | Promise<TReturn>;
  name?: string;
  budget?: Budget;
  approvals?: string[];
  onBudgetKill?: NotifyHandler;
  onRunComplete?: NotifyHandler;
  notify?: NotifyHandler[];
  traceDir?: string;
  autoPatch?: boolean;
  /** How to handle patch verification failures: "block" | "warn" | "allow". Default: "warn". */
  onPatchFailure?: "block" | "warn" | "allow";
  /** MCP tool names that must pass human approval before executing. */
  mcpApprovalRequired?: string[];
  /** Handler invoked when an MCP tool in the allowlist is called. */
  mcpApprovalHandler?: ApprovalHandler;
}

export class WickdAgent<TArgs extends unknown[], TReturn> {
  readonly fn: (...args: TArgs) => TReturn | Promise<TReturn>;
  readonly name: string;
  readonly budget?: Budget;
  readonly approvals: string[];
  readonly onBudgetKill?: NotifyHandler;
  readonly onRunComplete?: NotifyHandler;
  readonly notifyHandlers: NotifyHandler[];
  readonly traceStore: TraceStore;
  readonly mcpApprovalRequired: string[];
  readonly mcpApprovalHandler?: ApprovalHandler;

  constructor(options: AgentOptions<TArgs, TReturn>) {
    this.fn = options.fn;
    this.name = options.name ?? options.fn.name ?? "agent";
    this.budget = options.budget;
    this.approvals = options.approvals ?? [];
    this.onBudgetKill = options.onBudgetKill;
    this.onRunComplete = options.onRunComplete;
    this.notifyHandlers = options.notify ?? [];
    this.traceStore = new TraceStore(options.traceDir);
    this.mcpApprovalRequired = options.mcpApprovalRequired ?? [];
    this.mcpApprovalHandler = options.mcpApprovalHandler;

    if (options.autoPatch !== false) {
      patchAll();
      const failureMode = options.onPatchFailure ?? "warn";
      if (failureMode !== "allow") {
        this._checkPatches(failureMode);
      }
    }
  }

  private _checkPatches(failureMode: "block" | "warn"): void {
    const status = verifyPatches();
    const failed = Object.entries(status)
      .filter(([, info]) => info.installed && !info.verified)
      .map(([provider]) => provider);
    if (failed.length === 0) return;

    if (failureMode === "block") {
      throw new WickdPatchError(failed, status);
    }
    const providers = failed.join(", ");
    process.stderr.write(
      `[wickd] WARNING: Patch verification failed for: ${providers}. ` +
      `Budget enforcement may not be active for these providers.\n`,
    );
  }

  async run(...args: TArgs): Promise<TReturn> {
    const tracker = this.budget ? new BudgetTracker(this.budget) : null;

    const taskPreview = args.length > 0 && typeof args[0] === "string"
      ? (args[0] as string).slice(0, 200)
      : "";
    const trace = new Trace(this.name, taskPreview);

    let result: TReturn;

    const runInner = () => this.fn(...args);
    const runScoped = this.mcpApprovalRequired.length > 0 || this.mcpApprovalHandler
      ? () => {
          // eslint-disable-next-line @typescript-eslint/no-var-requires
          const { runWithMcpApprovalConfig } = require("./mcpPatch.js") as typeof import("./mcpPatch.js");
          return runWithMcpApprovalConfig(
            { required: this.mcpApprovalRequired, handler: this.mcpApprovalHandler ?? null },
            runInner,
          );
        }
      : runInner;

    try {
      result = await runWithContext({ tracker, trace }, runScoped);
      trace.complete(tracker?.summary());
    } catch (err) {
      if (err instanceof BudgetExceeded) {
        trace.addBudgetKill(err.trigger, err.spent);
        trace.complete(tracker?.summary());
        await this._fireBudgetKill(err, tracker, trace);
        throw err;
      }

      trace.status = "error";
      trace.addEvent(new TraceEvent("error", {
        errorMessage: String(err),
        spendAtEvent: trace.totalCost,
      }));
      trace.complete(tracker?.summary());
      throw err;
    } finally {
      this.traceStore.save(trace);
      this._printSummary(tracker, trace);
      await this._fireRunComplete(tracker, trace);
    }

    return result;
  }

  private async _fireBudgetKill(err: BudgetExceeded, tracker: BudgetTracker | null, trace: Trace): Promise<void> {
    const event: NotificationEvent = {
      type: "budget_kill",
      agentName: this.name,
      spend: err.spent,
      trigger: err.trigger,
      traceId: trace.traceId,
    };

    for (const handler of this.notifyHandlers) {
      try { await handler(event); } catch (e) { process.stderr.write(`wickd: handler error: ${e}\n`); }
    }

    if (this.onBudgetKill) {
      try {
        await this.onBudgetKill({ ...event, summary: tracker?.summary() });
      } catch (e) { process.stderr.write(`wickd: handler error: ${e}\n`); }
    }
  }

  private async _fireRunComplete(tracker: BudgetTracker | null, trace: Trace): Promise<void> {
    const event: NotificationEvent = {
      type: "run_complete",
      agentName: this.name,
      status: trace.status,
      summary: tracker?.summary(),
      traceId: trace.traceId,
    };

    for (const handler of this.notifyHandlers) {
      try { await handler(event); } catch (e) { process.stderr.write(`wickd: handler error: ${e}\n`); }
    }

    if (this.onRunComplete) {
      try { await this.onRunComplete(event); } catch (e) { process.stderr.write(`wickd: handler error: ${e}\n`); }
    }
  }

  private _printSummary(tracker: BudgetTracker | null, trace: Trace): void {
    const icons: Record<string, string> = {
      completed: "\x1b[32m\u2713\x1b[0m",
      budget_killed: "\x1b[31m\u2717 KILLED\x1b[0m",
      error: "\x1b[31m\u2717 ERROR\x1b[0m",
    };
    const icon = icons[trace.status] ?? "?";

    let budgetStr = "";
    if (tracker && this.budget?.perRun) {
      budgetStr = ` | budget: $${tracker.runSpend.toFixed(4)}/$${this.budget.perRun.toFixed(2)}`;
    }

    const durationStr = trace.durationMs() ? ` | ${trace.durationMs()!.toFixed(0)}ms` : "";
    const llmCalls = trace.events.filter(e => e.eventType === "llm_call").length;

    process.stderr.write(
      `[wickd] ${icon} ${trace.agentName} \u2014 $${trace.totalCost.toFixed(4)} cost | ${llmCalls} calls${budgetStr}${durationStr} | trace: ${trace.traceId.slice(0, 8)}\n`,
    );
  }
}

/** Create a Wickd-guarded agent. */
export function agent<TArgs extends unknown[], TReturn>(
  options: AgentOptions<TArgs, TReturn>,
): WickdAgent<TArgs, TReturn> {
  return new WickdAgent(options);
}
