export type RunStatus = "running" | "completed" | "failed" | "cancelled";

export type TerminalRunStatus = Exclude<RunStatus, "running">;

export type SpanKind = "llm" | "mcp_tool" | "http";

export type SpanStatus = "pending" | "ok" | "error";

export type TerminalSpanStatus = Exclude<SpanStatus, "pending">;

export interface Run {
  readonly id: string;
  readonly name: string;
  readonly startedAt: Date;
  readonly endedAt: Date | null;
  readonly status: RunStatus;
  readonly error: string | null;
  readonly totalCostUsd: number;
  readonly metadata: Readonly<Record<string, unknown>>;
}

export interface Span {
  readonly id: string;
  readonly runId: string;
  readonly parentSpanId: string | null;
  readonly kind: SpanKind;
  readonly name: string;
  readonly startedAt: Date;
  readonly endedAt: Date | null;
  readonly status: SpanStatus;
  readonly error: string | null;
  readonly input: Readonly<Record<string, unknown>>;
  readonly output: Readonly<Record<string, unknown>>;
  readonly costUsd: number;
  readonly tokensInput: number | null;
  readonly tokensOutput: number | null;
  readonly provider: string | null;
  readonly model: string | null;
  readonly metadata: Readonly<Record<string, unknown>>;
}

export interface Pricing {
  readonly provider: string;
  readonly model: string;
  readonly inputPerMillion: number;
  readonly outputPerMillion: number;
}
