import type { Run, Store } from "wickd-core";

export interface RunTrackerOptions {
  readonly runName: string;
  readonly idleTimeoutMs: number;
  /**
   * Clock override for tests. Defaults to `Date.now`.
   */
  readonly clock?: () => number;
}

/**
 * Tracks which run a proxied request should attribute to.
 *
 * Two modes, both transparent to the caller:
 *
 * 1. **Explicit attribution.** If the request carries a `x-wickd-run-id`
 *    header and the run exists, spans attach to that run without touching
 *    session state.
 *
 * 2. **Session attribution (default).** Consecutive requests within
 *    `idleTimeoutMs` share one run. When the gap exceeds the timeout,
 *    the previous run is closed and a new one is started.
 */
export class RunTracker {
  private current: Run | null = null;
  private lastActivity = 0;
  private readonly clock: () => number;

  constructor(
    private readonly store: Store,
    private readonly options: RunTrackerOptions,
  ) {
    this.clock = options.clock ?? Date.now;
  }

  attach(explicitRunId: string | null): Run {
    if (explicitRunId !== null) {
      const existing = this.store.getRun(explicitRunId);
      if (existing !== null) return existing;
      // Unknown id → fall through to session attribution.
    }
    return this.session();
  }

  /**
   * Currently tracked session run, or `null` if none is active.
   * Primarily exposed for introspection and tests.
   */
  get currentRun(): Run | null {
    return this.current;
  }

  /**
   * Close the active session run, if any. Call on graceful shutdown.
   * Safe to call multiple times.
   */
  shutdown(): void {
    this.finalize();
  }

  private session(): Run {
    const now = this.clock();
    if (
      this.current === null ||
      now - this.lastActivity > this.options.idleTimeoutMs
    ) {
      this.finalize();
      this.current = this.store.startRun({ name: this.options.runName });
    }
    this.lastActivity = now;
    return this.current;
  }

  private finalize(): void {
    if (this.current === null) return;
    try {
      this.store.endRun(this.current.id, { status: "completed" });
    } catch {
      // Run was already terminal (e.g., another process ended it, or tests
      // tore down the store). Ignoring is safe because finalize() only
      // exists to tidy up graceful exits — we don't block shutdown on it.
    }
    this.current = null;
  }
}
