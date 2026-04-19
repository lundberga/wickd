/**
 * Wickd core types — shared between SDKs.
 */

// ── Budget ──────────────────────────────────────────────────────────────────

export interface BudgetConfig {
  /** Max USD spend per single agent run. */
  perRun?: number;
  /** Max USD spend per calendar day across all runs. */
  daily?: number;
  /** Max USD spend per calendar month across all runs. */
  monthly?: number;
  /** Hard cap on LLM round-trips per run. Runaway-loop guard. */
  maxLlmCalls?: number;
  /** Hard cap on tool/MCP invocations per run. Runaway-loop guard. */
  maxToolCalls?: number;
  /** Hard cap on wall-clock seconds per run. Runaway-loop guard. */
  maxDurationSeconds?: number;
}

export interface BudgetSummary {
  runSpend: number;
  dailySpend: number;
  monthlySpend: number;
  callCount: number;
  toolCallCount: number;
  elapsedSeconds: number;
  remaining: number | null;
  killed: boolean;
  caps: BudgetConfig;
}

// ── Trace ───────────────────────────────────────────────────────────────────

export type TraceStatus = "running" | "completed" | "budget_killed" | "error";

export type TraceEventType =
  | "llm_call"
  | "tool_call"
  | "approval_gate"
  | "budget_kill"
  | "error";

export interface TraceEvent {
  eventType: TraceEventType;
  timestamp: number;
  eventId: string;

  // LLM call fields
  model?: string;
  provider?: string;
  inputTokens?: number;
  outputTokens?: number;
  cost?: number;
  latencyMs?: number;
  promptPreview?: string;
  responsePreview?: string;

  // Tool call fields (MCP or function call)
  toolName?: string;
  toolInputPreview?: string;
  toolOutputPreview?: string;
  toolStatus?: "success" | "error" | "denied";
  toolServer?: string;

  // Approval fields
  approvalName?: string;
  approvalStatus?: "approved" | "denied" | "pending";

  // Budget fields
  budgetTrigger?: string;
  spendAtEvent?: number;

  // Error fields
  errorMessage?: string;

  // Generic
  metadata?: Record<string, unknown>;
}

export interface Trace {
  traceId: string;
  agentName: string;
  task: string;
  startedAt: string; // ISO 8601
  endedAt?: string;  // ISO 8601
  durationMs?: number;
  status: TraceStatus;
  totalCost: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  llmCalls: number;
  toolCalls?: number;
  events: TraceEvent[];
  budgetSummary?: BudgetSummary;
}

// ── Approval ────────────────────────────────────────────────────────────────

export interface ApprovalRequest {
  type: "approval_request";
  name: string;
  function: string;
  argsPreview?: string;
  kwargsPreview?: string;
  traceId: string;
  requestedAt?: number;
}

export type ApprovalDecision = "approved" | "denied" | "pending";

export interface ApprovalResponse {
  status: ApprovalDecision;
  decidedBy?: string;
  decidedAt?: string;
  reason?: string;
}

// ── Notifications ───────────────────────────────────────────────────────────

export type NotificationEventType =
  | "budget_kill"
  | "approval_request"
  | "run_complete";

export interface NotificationEvent {
  type: NotificationEventType;
  agentName: string;
  traceId: string;
  spend?: number;
  trigger?: string;
  status?: string;
  summary?: BudgetSummary;
}
