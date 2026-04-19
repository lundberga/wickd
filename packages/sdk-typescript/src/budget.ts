import type { BudgetConfig, BudgetSummary } from "wickd-core";

const NON_COST_TRIGGERS = new Set(["max_llm_calls", "max_tool_calls", "max_duration"]);

export class BudgetExceeded extends Error {
  readonly budget: BudgetConfig;
  readonly spent: number;
  readonly trigger: string;
  readonly measured?: number;

  constructor(budget: BudgetConfig, spent: number, trigger: string, measured?: number) {
    const msg = BudgetExceeded.buildMessage(budget, spent, trigger, measured);
    super(msg);
    this.name = "BudgetExceeded";
    this.budget = budget;
    this.spent = spent;
    this.trigger = trigger;
    this.measured = measured;
  }

  private static buildMessage(
    budget: BudgetConfig,
    spent: number,
    trigger: string,
    measured?: number,
  ): string {
    if (NON_COST_TRIGGERS.has(trigger)) {
      if (trigger === "max_duration") {
        const cap = budget.maxDurationSeconds;
        const capStr = cap != null ? cap.toFixed(1) : "?";
        const measuredStr = measured != null ? measured.toFixed(1) : "?";
        return `Runaway guard tripped: max_duration=${measuredStr}s exceeded cap of ${capStr}s.`;
      }
      const cap =
        trigger === "max_llm_calls" ? budget.maxLlmCalls : budget.maxToolCalls;
      const capStr = cap != null ? String(cap) : "?";
      const measuredStr = measured != null ? String(measured) : "?";
      return `Runaway guard tripped: ${trigger}=${measuredStr} calls exceeded cap of ${capStr} calls.`;
    }

    const capValue = (budget as Record<string, unknown>)[trigger];
    if (typeof capValue === "number") {
      return `Budget exceeded: $${spent.toFixed(4)} spent, ${trigger} cap of $${capValue.toFixed(2)} hit. Agent execution killed.`;
    }
    return `Budget exceeded: $${spent.toFixed(4)} spent. Agent execution killed (${trigger}).`;
  }
}

export class WickdPatchError extends Error {
  readonly failedProviders: string[];
  readonly status: Record<string, unknown>;

  constructor(failedProviders: string[], status: Record<string, unknown>) {
    const providers = failedProviders.join(", ");
    super(
      `Wickd patch verification failed for: ${providers}. ` +
      `Budget enforcement is NOT active for these providers. ` +
      `Set onPatchFailure='warn' to downgrade to a warning, ` +
      `or onPatchFailure='allow' to run unprotected.`
    );
    this.name = "WickdPatchError";
    this.failedProviders = failedProviders;
    this.status = status;
  }
}

export interface BudgetOptions extends BudgetConfig {
  onKill?: (summary: BudgetSummary) => void;
}

export class Budget {
  readonly perRun?: number;
  readonly daily?: number;
  readonly monthly?: number;
  readonly maxLlmCalls?: number;
  readonly maxToolCalls?: number;
  readonly maxDurationSeconds?: number;
  readonly onKill?: (summary: BudgetSummary) => void;

  constructor(config: BudgetOptions) {
    if (config.perRun != null && config.perRun <= 0) throw new Error("perRun budget must be positive");
    if (config.daily != null && config.daily <= 0) throw new Error("daily budget must be positive");
    if (config.monthly != null && config.monthly <= 0) throw new Error("monthly budget must be positive");
    for (const [key, val] of [
      ["maxLlmCalls", config.maxLlmCalls],
      ["maxToolCalls", config.maxToolCalls],
    ] as const) {
      if (val != null) {
        if (!Number.isInteger(val) || val <= 0) {
          throw new Error(`${key} must be a positive integer`);
        }
      }
    }
    if (config.maxDurationSeconds != null && config.maxDurationSeconds <= 0) {
      throw new Error("maxDurationSeconds must be positive");
    }
    this.perRun = config.perRun;
    this.daily = config.daily;
    this.monthly = config.monthly;
    this.maxLlmCalls = config.maxLlmCalls;
    this.maxToolCalls = config.maxToolCalls;
    this.maxDurationSeconds = config.maxDurationSeconds;
    this.onKill = config.onKill;
  }
}

export class BudgetTracker {
  readonly budget: Budget;
  private _runSpend = 0;
  private _dailySpend = 0;
  private _monthlySpend = 0;
  private _callCount = 0;
  private _toolCallCount = 0;
  private _killed = false;
  private readonly _runStart: number = Date.now();

  constructor(budget: Budget) {
    this.budget = budget;
  }

  get runSpend(): number { return this._runSpend; }
  get dailySpend(): number { return this._dailySpend; }
  get callCount(): number { return this._callCount; }
  get toolCallCount(): number { return this._toolCallCount; }
  get elapsedSeconds(): number { return (Date.now() - this._runStart) / 1000; }
  get isKilled(): boolean { return this._killed; }

  remaining(): number | null {
    const remainders: number[] = [];
    if (this.budget.perRun != null) remainders.push(this.budget.perRun - this._runSpend);
    if (this.budget.daily != null) remainders.push(this.budget.daily - this._dailySpend);
    if (this.budget.monthly != null) remainders.push(this.budget.monthly - this._monthlySpend);
    return remainders.length > 0 ? Math.min(...remainders) : null;
  }

  checkBudget(): void {
    if (this._killed) {
      throw new BudgetExceeded(this.budget, this._runSpend, "already_killed");
    }
    if (this.budget.perRun != null && this._runSpend >= this.budget.perRun) {
      this._kill("perRun");
    }
    if (this.budget.daily != null && this._dailySpend >= this.budget.daily) {
      this._kill("daily");
    }
    if (this.budget.monthly != null && this._monthlySpend >= this.budget.monthly) {
      this._kill("monthly");
    }

    // Non-cost runaway guards.
    if (
      this.budget.maxLlmCalls != null &&
      this._callCount >= this.budget.maxLlmCalls
    ) {
      this._kill("max_llm_calls", this._callCount);
    }
    if (
      this.budget.maxToolCalls != null &&
      this._toolCallCount >= this.budget.maxToolCalls
    ) {
      this._kill("max_tool_calls", this._toolCallCount);
    }
    if (this.budget.maxDurationSeconds != null) {
      const elapsed = this.elapsedSeconds;
      if (elapsed >= this.budget.maxDurationSeconds) {
        this._kill("max_duration", elapsed);
      }
    }
  }

  recordCost(cost: number): void {
    this._runSpend += cost;
    this._dailySpend += cost;
    this._monthlySpend += cost;
    this._callCount += 1;
    this.checkBudget();
  }

  recordToolCall(): void {
    this._toolCallCount += 1;
    this.checkBudget();
  }

  preCallCheck(): void {
    this.checkBudget();
  }

  resetRun(): void {
    this._runSpend = 0;
    this._callCount = 0;
    this._toolCallCount = 0;
    this._killed = false;
  }

  summary(): BudgetSummary {
    return {
      runSpend: Math.round(this._runSpend * 1_000_000) / 1_000_000,
      dailySpend: Math.round(this._dailySpend * 1_000_000) / 1_000_000,
      monthlySpend: Math.round(this._monthlySpend * 1_000_000) / 1_000_000,
      callCount: this._callCount,
      toolCallCount: this._toolCallCount,
      elapsedSeconds: Math.round(this.elapsedSeconds * 1000) / 1000,
      remaining: this.remaining(),
      killed: this._killed,
      caps: {
        perRun: this.budget.perRun,
        daily: this.budget.daily,
        monthly: this.budget.monthly,
        maxLlmCalls: this.budget.maxLlmCalls,
        maxToolCalls: this.budget.maxToolCalls,
        maxDurationSeconds: this.budget.maxDurationSeconds,
      },
    };
  }

  private _kill(trigger: string, measured?: number): void {
    this._killed = true;
    // Must not let onKill errors mask the BudgetExceeded throw
    try { this.budget.onKill?.(this.summary()); } catch (e) { process.stderr.write(`wickd: handler error: ${e}\n`); }
    throw new BudgetExceeded(this.budget, this._runSpend, trigger, measured);
  }
}
