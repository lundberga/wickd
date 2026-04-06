import { Budget, BudgetTracker, BudgetExceeded, WickdPatchError } from "./budget.js";
import { Trace, TraceStore, TraceEvent, CloudTraceSync } from "./trace.js";
import { patchAll, verifyPatches, runWithContext } from "./interceptor.js";
import type { NotifyHandler } from "./notify.js";
import type { NotificationEvent, BudgetSummary } from "@wickdone/core";

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
  cloudEndpoint?: string;
  cloudApiKey?: string;
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
  private readonly _cloudSync: CloudTraceSync | null;
  private _tracker: BudgetTracker | null = null;
  private _trace: Trace | null = null;

  constructor(options: AgentOptions<TArgs, TReturn>) {
    this.fn = options.fn;
    this.name = options.name ?? options.fn.name ?? "agent";
    this.budget = options.budget;
    this.approvals = options.approvals ?? [];
    this.onBudgetKill = options.onBudgetKill;
    this.onRunComplete = options.onRunComplete;
    this.notifyHandlers = options.notify ?? [];
    this.traceStore = new TraceStore(options.traceDir);

    // Cloud sync: explicit options take precedence over env vars
    const endpoint = options.cloudEndpoint ?? process.env.WICKD_CLOUD_ENDPOINT;
    const apiKey = options.cloudApiKey ?? process.env.WICKD_API_KEY;
    this._cloudSync = endpoint && apiKey ? new CloudTraceSync(endpoint, apiKey) : null;

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

  get tracker(): BudgetTracker | null { return this._tracker; }
  get trace(): Trace | null { return this._trace; }

  async run(...args: TArgs): Promise<TReturn> {
    this._tracker = this.budget ? new BudgetTracker(this.budget) : null;

    const taskPreview = args.length > 0 && typeof args[0] === "string"
      ? (args[0] as string).slice(0, 200)
      : "";
    this._trace = new Trace(this.name, taskPreview);

    let result: TReturn;

    try {
      result = await runWithContext(
        { tracker: this._tracker, trace: this._trace },
        () => this.fn(...args),
      );
      this._trace.complete(this._tracker?.summary());
    } catch (err) {
      if (err instanceof BudgetExceeded) {
        this._trace.addBudgetKill(err.trigger, err.spent);
        this._trace.complete(this._tracker?.summary());
        await this._fireBudgetKill(err);
        throw err;
      }

      this._trace.status = "error";
      this._trace.addEvent(new TraceEvent("error", {
        errorMessage: String(err),
        spendAtEvent: this._trace.totalCost,
      }));
      this._trace.complete(this._tracker?.summary());
      throw err;
    } finally {
      this.traceStore.save(this._trace);
      if (this._cloudSync) {
        this._cloudSync.sync(this._trace);
      }
      this._printSummary();
      await this._fireRunComplete();
    }

    return result;
  }

  private async _fireBudgetKill(err: BudgetExceeded): Promise<void> {
    const event: NotificationEvent = {
      type: "budget_kill",
      agentName: this.name,
      spend: err.spent,
      trigger: err.trigger,
      traceId: this._trace?.traceId ?? "unknown",
    };

    for (const handler of this.notifyHandlers) {
      try { await handler(event); } catch { /* notification failures are non-fatal */ }
    }

    if (this.onBudgetKill) {
      try {
        await this.onBudgetKill({ ...event, summary: this._tracker?.summary() });
      } catch { /* notification failures are non-fatal */ }
    }
  }

  private async _fireRunComplete(): Promise<void> {
    if (!this._trace) return;

    const event: NotificationEvent = {
      type: "run_complete",
      agentName: this.name,
      status: this._trace.status,
      summary: this._tracker?.summary(),
      traceId: this._trace.traceId,
    };

    for (const handler of this.notifyHandlers) {
      try { await handler(event); } catch { /* notification failures are non-fatal */ }
    }

    if (this.onRunComplete) {
      try { await this.onRunComplete(event); } catch { /* notification failures are non-fatal */ }
    }
  }

  private _printSummary(): void {
    if (!this._trace) return;

    const t = this._trace;
    const icons: Record<string, string> = {
      completed: "\x1b[32m\u2713\x1b[0m",
      budget_killed: "\x1b[31m\u2717 KILLED\x1b[0m",
      error: "\x1b[31m\u2717 ERROR\x1b[0m",
    };
    const icon = icons[t.status] ?? "?";

    let budgetStr = "";
    if (this._tracker && this.budget?.perRun) {
      budgetStr = ` | budget: $${this._tracker.runSpend.toFixed(4)}/$${this.budget.perRun.toFixed(2)}`;
    }

    const durationStr = t.durationMs() ? ` | ${t.durationMs()!.toFixed(0)}ms` : "";
    const llmCalls = t.events.filter(e => e.eventType === "llm_call").length;

    process.stderr.write(
      `[wickd] ${icon} ${t.agentName} \u2014 $${t.totalCost.toFixed(4)} cost | ${llmCalls} calls${budgetStr}${durationStr} | trace: ${t.traceId.slice(0, 8)}\n`,
    );
  }
}

/** Create a Wickd-guarded agent. */
export function agent<TArgs extends unknown[], TReturn>(
  options: AgentOptions<TArgs, TReturn>,
): WickdAgent<TArgs, TReturn> {
  return new WickdAgent(options);
}
