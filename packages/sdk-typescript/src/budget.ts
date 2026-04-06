import type { BudgetConfig, BudgetSummary } from "wickd-core";

export class BudgetExceeded extends Error {
  readonly budget: BudgetConfig;
  readonly spent: number;
  readonly trigger: string;

  constructor(budget: BudgetConfig, spent: number, trigger: string) {
    const capValue = budget[trigger as keyof BudgetConfig];
    const msg = capValue != null
      ? `Budget exceeded: $${spent.toFixed(4)} spent, ${trigger} cap of $${capValue.toFixed(2)} hit. Agent execution killed.`
      : `Budget exceeded: $${spent.toFixed(4)} spent. Agent execution killed (${trigger}).`;
    super(msg);
    this.name = "BudgetExceeded";
    this.budget = budget;
    this.spent = spent;
    this.trigger = trigger;
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

export class Budget {
  readonly perRun?: number;
  readonly daily?: number;
  readonly monthly?: number;
  readonly onKill?: (summary: BudgetSummary) => void;

  constructor(config: BudgetConfig & { onKill?: (summary: BudgetSummary) => void }) {
    if (config.perRun != null && config.perRun <= 0) throw new Error("perRun budget must be positive");
    if (config.daily != null && config.daily <= 0) throw new Error("daily budget must be positive");
    if (config.monthly != null && config.monthly <= 0) throw new Error("monthly budget must be positive");
    this.perRun = config.perRun;
    this.daily = config.daily;
    this.monthly = config.monthly;
    this.onKill = config.onKill;
  }
}

export class BudgetTracker {
  readonly budget: Budget;
  private _runSpend = 0;
  private _dailySpend = 0;
  private _monthlySpend = 0;
  private _callCount = 0;
  private _killed = false;

  constructor(budget: Budget) {
    this.budget = budget;
  }

  get runSpend(): number { return this._runSpend; }
  get dailySpend(): number { return this._dailySpend; }
  get callCount(): number { return this._callCount; }
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
  }

  recordCost(cost: number, _model = "", _inputTokens = 0, _outputTokens = 0): void {
    this._runSpend += cost;
    this._dailySpend += cost;
    this._monthlySpend += cost;
    this._callCount += 1;
    this.checkBudget();
  }

  preCallCheck(): void {
    this.checkBudget();
  }

  resetRun(): void {
    this._runSpend = 0;
    this._callCount = 0;
    this._killed = false;
  }

  summary(): BudgetSummary {
    return {
      runSpend: Math.round(this._runSpend * 1_000_000) / 1_000_000,
      dailySpend: Math.round(this._dailySpend * 1_000_000) / 1_000_000,
      monthlySpend: Math.round(this._monthlySpend * 1_000_000) / 1_000_000,
      callCount: this._callCount,
      remaining: this.remaining(),
      killed: this._killed,
      caps: {
        perRun: this.budget.perRun,
        daily: this.budget.daily,
        monthly: this.budget.monthly,
      },
    };
  }

  private _kill(trigger: string): void {
    this._killed = true;
    // Must not let onKill errors mask the BudgetExceeded throw
    try { this.budget.onKill?.(this.summary()); } catch { /* intentional */ }
    throw new BudgetExceeded(this.budget, this._runSpend, trigger);
  }
}
