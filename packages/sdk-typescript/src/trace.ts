import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import type {
  TraceEvent as CoreTraceEvent,
  TraceEventType,
  TraceStatus,
  Trace as CoreTrace,
  BudgetSummary,
} from "wickd-core";

export class TraceEvent {
  eventType: TraceEventType;
  timestamp: number;
  eventId: string;
  model?: string;
  provider?: string;
  inputTokens?: number;
  outputTokens?: number;
  cost?: number;
  latencyMs?: number;
  promptPreview?: string;
  responsePreview?: string;
  // Tool call (MCP or function call)
  toolName?: string;
  toolInputPreview?: string;
  toolOutputPreview?: string;
  toolStatus?: "success" | "error" | "denied";
  toolServer?: string;

  approvalName?: string;
  approvalStatus?: "approved" | "denied" | "pending";
  budgetTrigger?: string;
  spendAtEvent?: number;
  errorMessage?: string;
  metadata?: Record<string, unknown>;

  constructor(eventType: TraceEventType, fields: Partial<Omit<TraceEvent, "eventType">> = {}) {
    this.eventType = eventType;
    this.timestamp = fields.timestamp ?? Date.now() / 1000;
    this.eventId = fields.eventId ?? randomUUID().slice(0, 8);
    Object.assign(this, fields);
  }

  toDict(): CoreTraceEvent {
    const result: CoreTraceEvent = {
      eventType: this.eventType,
      timestamp: this.timestamp,
      eventId: this.eventId,
    };
    if (this.model != null) result.model = this.model;
    if (this.provider != null) result.provider = this.provider;
    if (this.inputTokens != null) result.inputTokens = this.inputTokens;
    if (this.outputTokens != null) result.outputTokens = this.outputTokens;
    if (this.cost != null) result.cost = this.cost;
    if (this.latencyMs != null) result.latencyMs = this.latencyMs;
    if (this.promptPreview != null) result.promptPreview = this.promptPreview;
    if (this.responsePreview != null) result.responsePreview = this.responsePreview;
    if (this.toolName != null) (result as unknown as Record<string, unknown>).toolName = this.toolName;
    if (this.toolInputPreview != null) (result as unknown as Record<string, unknown>).toolInputPreview = this.toolInputPreview;
    if (this.toolOutputPreview != null) (result as unknown as Record<string, unknown>).toolOutputPreview = this.toolOutputPreview;
    if (this.toolStatus != null) (result as unknown as Record<string, unknown>).toolStatus = this.toolStatus;
    if (this.toolServer != null) (result as unknown as Record<string, unknown>).toolServer = this.toolServer;
    if (this.approvalName != null) result.approvalName = this.approvalName;
    if (this.approvalStatus != null) result.approvalStatus = this.approvalStatus;
    if (this.budgetTrigger != null) result.budgetTrigger = this.budgetTrigger;
    if (this.spendAtEvent != null) result.spendAtEvent = this.spendAtEvent;
    if (this.errorMessage != null) result.errorMessage = this.errorMessage;
    if (this.metadata != null) result.metadata = this.metadata;
    return result;
  }
}

export class Trace {
  traceId: string;
  agentName: string;
  task: string;
  startedAt: number;
  endedAt?: number;
  events: TraceEvent[] = [];
  totalCost = 0;
  totalInputTokens = 0;
  totalOutputTokens = 0;
  status: TraceStatus = "running";
  budgetSummary?: BudgetSummary;

  constructor(agentName = "", task = "") {
    this.traceId = randomUUID();
    this.agentName = agentName;
    this.task = task;
    this.startedAt = Date.now() / 1000;
  }

  addEvent(event: TraceEvent): void {
    this.events.push(event);
    if (event.cost != null) this.totalCost += event.cost;
    if (event.inputTokens != null) this.totalInputTokens += event.inputTokens;
    if (event.outputTokens != null) this.totalOutputTokens += event.outputTokens;
  }

  addLlmCall(fields: {
    model: string;
    provider: string;
    inputTokens: number;
    outputTokens: number;
    cost: number;
    latencyMs: number;
    promptPreview?: string;
    responsePreview?: string;
  }): TraceEvent {
    const event = new TraceEvent("llm_call", {
      model: fields.model,
      provider: fields.provider,
      inputTokens: fields.inputTokens,
      outputTokens: fields.outputTokens,
      cost: fields.cost,
      latencyMs: fields.latencyMs,
      promptPreview: fields.promptPreview?.slice(0, 200),
      responsePreview: fields.responsePreview?.slice(0, 200),
      spendAtEvent: this.totalCost + fields.cost,
    });
    this.addEvent(event);
    return event;
  }

  addApproval(name: string, status: "approved" | "denied" | "pending"): TraceEvent {
    const event = new TraceEvent("approval_gate", {
      approvalName: name,
      approvalStatus: status,
      spendAtEvent: this.totalCost,
    });
    this.addEvent(event);
    return event;
  }

  addToolCall(fields: {
    toolName: string;
    latencyMs: number;
    toolInput?: string;
    toolOutput?: string;
    toolStatus?: "success" | "error" | "denied";
    toolServer?: string;
    metadata?: Record<string, unknown>;
  }): TraceEvent {
    const meta: Record<string, unknown> = { tool: fields.toolName, ...fields.metadata };
    if (fields.toolServer) meta.server = fields.toolServer;
    const event = new TraceEvent("tool_call", {
      toolName: fields.toolName,
      toolInputPreview: fields.toolInput?.slice(0, 200),
      toolOutputPreview: fields.toolOutput?.slice(0, 200),
      toolStatus: fields.toolStatus ?? "success",
      toolServer: fields.toolServer,
      latencyMs: fields.latencyMs,
      spendAtEvent: this.totalCost,
      metadata: meta,
    });
    this.addEvent(event);
    return event;
  }

  addBudgetKill(trigger: string, spend: number): TraceEvent {
    const event = new TraceEvent("budget_kill", {
      budgetTrigger: trigger,
      spendAtEvent: spend,
    });
    this.addEvent(event);
    this.status = "budget_killed";
    return event;
  }

  complete(budgetSummary?: BudgetSummary): void {
    this.endedAt = Date.now() / 1000;
    if (this.status === "running") this.status = "completed";
    this.budgetSummary = budgetSummary;
  }

  durationMs(): number | undefined {
    if (!this.endedAt) return undefined;
    return Math.round((this.endedAt - this.startedAt) * 1000 * 10) / 10;
  }

  toDict(): CoreTrace {
    return {
      traceId: this.traceId,
      agentName: this.agentName,
      task: this.task,
      startedAt: new Date(this.startedAt * 1000).toISOString(),
      endedAt: this.endedAt ? new Date(this.endedAt * 1000).toISOString() : undefined,
      durationMs: this.durationMs(),
      status: this.status,
      totalCost: Math.round(this.totalCost * 1_000_000) / 1_000_000,
      totalInputTokens: this.totalInputTokens,
      totalOutputTokens: this.totalOutputTokens,
      llmCalls: this.events.filter(e => e.eventType === "llm_call").length,
      toolCalls: this.events.filter(e => e.eventType === "tool_call").length,
      events: this.events.map(e => e.toDict()),
      budgetSummary: this.budgetSummary,
    };
  }

  toJSON(indent = 2): string {
    return JSON.stringify(this.toDict(), null, indent);
  }
}

export class TraceStore {
  readonly traceDir: string;

  constructor(traceDir?: string) {
    this.traceDir = traceDir ?? join(homedir(), ".wickd", "traces");
    mkdirSync(this.traceDir, { recursive: true });
  }

  save(trace: Trace): string {
    const filename = `${trace.agentName}_${trace.traceId.slice(0, 8)}.json`;
    const filepath = join(this.traceDir, filename);
    writeFileSync(filepath, trace.toJSON());
    return filepath;
  }

  listTraces(limit = 20): CoreTrace[] {
    try {
      const files = readdirSync(this.traceDir)
        .filter(f => f.endsWith(".json"))
        .map(f => ({ name: f, mtime: statSync(join(this.traceDir, f)).mtimeMs }))
        .sort((a, b) => b.mtime - a.mtime)
        .slice(0, limit);

      return files.map(({ name }) => {
        const raw = readFileSync(join(this.traceDir, name), "utf-8");
        return JSON.parse(raw);
      });
    } catch {
      return [];
    }
  }

  getTrace(traceId: string): CoreTrace | null {
    const files = readdirSync(this.traceDir);
    const match = files.find(f => f.includes(traceId));
    if (!match) return null;
    const raw = readFileSync(join(this.traceDir, match), "utf-8");
    return JSON.parse(raw);
  }
}

export class CloudTraceSync {
  private readonly endpoint: string;
  private readonly apiKey: string;

  constructor(endpoint: string, apiKey: string) {
    this.endpoint = endpoint.replace(/\/+$/, "");
    this.apiKey = apiKey;
  }

  /**
   * Fire-and-forget sync: sends trace to the cloud endpoint.
   * Does not block the agent. Errors are silently caught.
   */
  sync(trace: Trace): void {
    const body = JSON.stringify(trace.toDict());
    fetch(this.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.apiKey}`,
      },
      body,
    }).catch(() => {
      // Silently swallow errors — cloud sync is best-effort
    });
  }
}
