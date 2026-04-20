import Database from "better-sqlite3";
import type { Database as DB, Statement } from "better-sqlite3";

import { newId } from "./id.js";
import { migrate } from "./schema.js";
import { calculateCost, type PricingCatalog } from "./pricing.js";
import {
  InvalidStateError,
  RunNotFoundError,
  SpanNotFoundError,
} from "./errors.js";
import type {
  Run,
  RunStatus,
  Span,
  SpanKind,
  SpanStatus,
  TerminalRunStatus,
  TerminalSpanStatus,
} from "./types.js";

export interface StoreOptions {
  /** Open the database read-only. Skips migrations. */
  readonly readonly?: boolean;
  /** Override the default pricing catalog when auto-calculating span cost. */
  readonly pricingCatalog?: PricingCatalog;
}

export interface StartRunInput {
  readonly name: string;
  readonly metadata?: Record<string, unknown>;
}

export interface EndRunInput {
  readonly status: TerminalRunStatus;
  readonly error?: string | null;
}

export interface StartSpanInput {
  readonly runId: string;
  readonly kind: SpanKind;
  readonly name: string;
  readonly parentSpanId?: string | null;
  readonly provider?: string | null;
  readonly model?: string | null;
  readonly input?: Record<string, unknown>;
  readonly metadata?: Record<string, unknown>;
}

export interface EndSpanInput {
  readonly status: TerminalSpanStatus;
  readonly error?: string | null;
  readonly output?: Record<string, unknown>;
  readonly tokensInput?: number | null;
  readonly tokensOutput?: number | null;
  /**
   * Explicit cost in USD. When omitted, cost is computed from the span's
   * model and the provided token counts via the pricing catalog.
   */
  readonly costUsd?: number;
}

export interface ListRunsFilter {
  readonly status?: RunStatus;
  readonly since?: Date;
  readonly until?: Date;
  readonly limit?: number;
  readonly offset?: number;
}

interface RunRow {
  readonly id: string;
  readonly name: string;
  readonly started_at: number;
  readonly ended_at: number | null;
  readonly status: RunStatus;
  readonly error: string | null;
  readonly total_cost_usd: number;
  readonly metadata: string;
}

interface SpanRow {
  readonly id: string;
  readonly run_id: string;
  readonly parent_span_id: string | null;
  readonly kind: SpanKind;
  readonly name: string;
  readonly started_at: number;
  readonly ended_at: number | null;
  readonly status: SpanStatus;
  readonly error: string | null;
  readonly input: string;
  readonly output: string;
  readonly cost_usd: number;
  readonly tokens_input: number | null;
  readonly tokens_output: number | null;
  readonly provider: string | null;
  readonly model: string | null;
  readonly metadata: string;
}

const DEFAULT_LIST_LIMIT = 100;
const MAX_LIST_LIMIT = 10_000;

export class Store {
  private readonly db: DB;
  private readonly pricingCatalog: PricingCatalog | null;

  private readonly stmtInsertRun: Statement;
  private readonly stmtEndRun: Statement;
  private readonly stmtGetRun: Statement;
  private readonly stmtInsertSpan: Statement;
  private readonly stmtEndSpan: Statement;
  private readonly stmtBumpRunCost: Statement;
  private readonly stmtGetSpan: Statement;
  private readonly stmtSpansForRun: Statement;

  constructor(path: string, options: StoreOptions = {}) {
    this.db = new Database(path, { readonly: options.readonly === true });
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("foreign_keys = ON");
    this.db.pragma("synchronous = NORMAL");

    if (options.readonly !== true) {
      migrate(this.db);
    }
    this.pricingCatalog = options.pricingCatalog ?? null;

    this.stmtInsertRun = this.db.prepare(`
      INSERT INTO runs
        (id, name, started_at, ended_at, status, error, total_cost_usd, metadata)
      VALUES (?, ?, ?, NULL, 'running', NULL, 0, ?)
    `);
    this.stmtEndRun = this.db.prepare(`
      UPDATE runs SET status = ?, error = ?, ended_at = ?
      WHERE id = ? AND status = 'running'
    `);
    this.stmtGetRun = this.db.prepare("SELECT * FROM runs WHERE id = ?");

    this.stmtInsertSpan = this.db.prepare(`
      INSERT INTO spans (
        id, run_id, parent_span_id, kind, name,
        started_at, status, input, cost_usd,
        provider, model, metadata
      ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?)
    `);
    this.stmtEndSpan = this.db.prepare(`
      UPDATE spans SET
        status = ?, error = ?, ended_at = ?,
        output = ?, cost_usd = ?,
        tokens_input = ?, tokens_output = ?
      WHERE id = ? AND status = 'pending'
    `);
    this.stmtBumpRunCost = this.db.prepare(
      "UPDATE runs SET total_cost_usd = total_cost_usd + ? WHERE id = ?",
    );
    this.stmtGetSpan = this.db.prepare("SELECT * FROM spans WHERE id = ?");
    this.stmtSpansForRun = this.db.prepare(
      // rowid is SQLite's implicit insertion-order counter. Using it as the
      // tiebreaker guarantees stable ordering even when two spans share a
      // millisecond timestamp.
      "SELECT * FROM spans WHERE run_id = ? ORDER BY started_at ASC, rowid ASC",
    );
  }

  startRun(input: StartRunInput): Run {
    const id = newId();
    const startedAt = Date.now();
    const metadata = input.metadata ?? {};
    this.stmtInsertRun.run(id, input.name, startedAt, JSON.stringify(metadata));
    return {
      id,
      name: input.name,
      startedAt: new Date(startedAt),
      endedAt: null,
      status: "running",
      error: null,
      totalCostUsd: 0,
      metadata,
    };
  }

  endRun(id: string, input: EndRunInput): Run {
    const endedAt = Date.now();
    const result = this.stmtEndRun.run(
      input.status,
      input.error ?? null,
      endedAt,
      id,
    );
    if (result.changes === 0) {
      const existing = this.getRun(id);
      if (existing === null) throw new RunNotFoundError(id);
      throw new InvalidStateError(
        `Cannot end run ${id}: status is '${existing.status}', not 'running'`,
      );
    }
    const updated = this.getRun(id);
    // Safe: row existed one statement ago and nothing deletes it here.
    if (updated === null) throw new RunNotFoundError(id);
    return updated;
  }

  getRun(id: string): Run | null {
    const row = this.stmtGetRun.get(id) as RunRow | undefined;
    return row === undefined ? null : rowToRun(row);
  }

  listRuns(filter: ListRunsFilter = {}): Run[] {
    const where: string[] = [];
    const params: unknown[] = [];

    if (filter.status !== undefined) {
      where.push("status = ?");
      params.push(filter.status);
    }
    if (filter.since !== undefined) {
      where.push("started_at >= ?");
      params.push(filter.since.getTime());
    }
    if (filter.until !== undefined) {
      where.push("started_at <= ?");
      params.push(filter.until.getTime());
    }

    const limit = clampLimit(filter.limit);
    const offset = Math.max(0, filter.offset ?? 0);

    const sql = [
      "SELECT * FROM runs",
      where.length > 0 ? `WHERE ${where.join(" AND ")}` : "",
      "ORDER BY started_at DESC",
      "LIMIT ? OFFSET ?",
    ]
      .filter((p) => p.length > 0)
      .join(" ");

    params.push(limit, offset);

    const rows = this.db.prepare(sql).all(...params) as readonly RunRow[];
    return rows.map(rowToRun);
  }

  startSpan(input: StartSpanInput): Span {
    const id = newId();
    const startedAt = Date.now();
    const spanInput = input.input ?? {};
    const metadata = input.metadata ?? {};
    const parentSpanId = input.parentSpanId ?? null;
    const provider = input.provider ?? null;
    const model = input.model ?? null;

    this.stmtInsertSpan.run(
      id,
      input.runId,
      parentSpanId,
      input.kind,
      input.name,
      startedAt,
      JSON.stringify(spanInput),
      provider,
      model,
      JSON.stringify(metadata),
    );

    return {
      id,
      runId: input.runId,
      parentSpanId,
      kind: input.kind,
      name: input.name,
      startedAt: new Date(startedAt),
      endedAt: null,
      status: "pending",
      error: null,
      input: spanInput,
      output: {},
      costUsd: 0,
      tokensInput: null,
      tokensOutput: null,
      provider,
      model,
      metadata,
    };
  }

  endSpan(id: string, input: EndSpanInput): Span {
    const current = this.getSpan(id);
    if (current === null) throw new SpanNotFoundError(id);
    if (current.status !== "pending") {
      throw new InvalidStateError(
        `Cannot end span ${id}: status is '${current.status}', not 'pending'`,
      );
    }

    const tokensInput = input.tokensInput ?? null;
    const tokensOutput = input.tokensOutput ?? null;
    const costUsd = resolveSpanCost(
      input.costUsd,
      current.model,
      tokensInput,
      tokensOutput,
      this.pricingCatalog,
    );
    const endedAt = Date.now();
    const output = JSON.stringify(input.output ?? {});

    const tx = this.db.transaction(() => {
      const res = this.stmtEndSpan.run(
        input.status,
        input.error ?? null,
        endedAt,
        output,
        costUsd,
        tokensInput,
        tokensOutput,
        id,
      );
      if (res.changes === 0) {
        throw new InvalidStateError(
          `Cannot end span ${id}: concurrent modification detected`,
        );
      }
      if (costUsd !== 0) {
        this.stmtBumpRunCost.run(costUsd, current.runId);
      }
    });
    tx();

    const updated = this.getSpan(id);
    if (updated === null) throw new SpanNotFoundError(id);
    return updated;
  }

  getSpan(id: string): Span | null {
    const row = this.stmtGetSpan.get(id) as SpanRow | undefined;
    return row === undefined ? null : rowToSpan(row);
  }

  getSpansForRun(runId: string): Span[] {
    const rows = this.stmtSpansForRun.all(runId) as readonly SpanRow[];
    return rows.map(rowToSpan);
  }

  /**
   * Underlying SQLite handle, exposed for tests and advanced migrations.
   * Prefer the typed methods above for normal use.
   */
  get raw(): DB {
    return this.db;
  }

  close(): void {
    this.db.close();
  }
}

function rowToRun(row: RunRow): Run {
  return {
    id: row.id,
    name: row.name,
    startedAt: new Date(row.started_at),
    endedAt: row.ended_at === null ? null : new Date(row.ended_at),
    status: row.status,
    error: row.error,
    totalCostUsd: row.total_cost_usd,
    metadata: parseJsonObject(row.metadata),
  };
}

function rowToSpan(row: SpanRow): Span {
  return {
    id: row.id,
    runId: row.run_id,
    parentSpanId: row.parent_span_id,
    kind: row.kind,
    name: row.name,
    startedAt: new Date(row.started_at),
    endedAt: row.ended_at === null ? null : new Date(row.ended_at),
    status: row.status,
    error: row.error,
    input: parseJsonObject(row.input),
    output: parseJsonObject(row.output),
    costUsd: row.cost_usd,
    tokensInput: row.tokens_input,
    tokensOutput: row.tokens_output,
    provider: row.provider,
    model: row.model,
    metadata: parseJsonObject(row.metadata),
  };
}

function parseJsonObject(raw: string): Record<string, unknown> {
  const parsed = JSON.parse(raw) as unknown;
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return {};
  }
  return parsed as Record<string, unknown>;
}

function resolveSpanCost(
  explicit: number | undefined,
  model: string | null,
  tokensInput: number | null,
  tokensOutput: number | null,
  catalog: PricingCatalog | null,
): number {
  if (explicit !== undefined) return explicit;
  if (model === null || tokensInput === null || tokensOutput === null) return 0;
  return calculateCost(model, tokensInput, tokensOutput, catalog);
}

function clampLimit(limit: number | undefined): number {
  if (limit === undefined) return DEFAULT_LIST_LIMIT;
  if (!Number.isFinite(limit) || limit <= 0) return DEFAULT_LIST_LIMIT;
  return Math.min(MAX_LIST_LIMIT, Math.floor(limit));
}
