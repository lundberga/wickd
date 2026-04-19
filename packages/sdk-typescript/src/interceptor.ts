/**
 * Intercepts LLM SDK calls for budget tracking, cost attribution, and tracing.
 *
 * Patches OpenAI, Anthropic, and Google GenAI at the prototype level.
 * Supports both streaming and non-streaming responses.
 */

import { calculateCost } from "wickd-core";
import { Trace } from "./trace.js";
import { BudgetTracker } from "./budget.js";
import { AsyncLocalStorage } from "node:async_hooks";

const MAX_PREVIEW = 200;
const SENTINEL = "_wickdPatched";

// ── Context ───────────────────────────────────────────────────────────────

interface WickdContext {
  tracker: BudgetTracker | null;
  trace: Trace | null;
}

const storage = new AsyncLocalStorage<WickdContext>();

export function runWithContext<T>(ctx: WickdContext, fn: () => T): T {
  return storage.run(ctx, fn);
}

export function getActiveTracker(): BudgetTracker | null {
  return storage.getStore()?.tracker ?? null;
}

export function getActiveTrace(): Trace | null {
  return storage.getStore()?.trace ?? null;
}

/** Increment the active tracker's tool-call counter, enforcing runaway guards.
 *
 * No-op when no tracker is active. Called from `tools.ts` and `mcpPatch.ts`
 * after a tool has run.
 */
export function recordToolCallOnTracker(): void {
  const tracker = getActiveTracker();
  if (tracker !== null) {
    tracker.recordToolCall();
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────

function safeGet(obj: unknown, ...keys: string[]): unknown {
  let current = obj;
  for (const key of keys) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function preview(value: unknown): string {
  return typeof value === "string" ? value.slice(0, MAX_PREVIEW) : "";
}

function lastMessageContent(args: unknown[]): string {
  const body = (args[0] ?? {}) as Record<string, unknown>;
  const messages = (body.messages ?? []) as Array<Record<string, unknown>>;
  const last = messages[messages.length - 1];
  if (!last) return "";
  const content = last.content;
  if (typeof content === "string") return content.slice(0, MAX_PREVIEW);
  if (Array.isArray(content)) {
    const text = content.find((b: Record<string, unknown>) => b.type === "text") as Record<string, unknown> | undefined;
    return preview(text?.text);
  }
  return "";
}

// ── Provider extractors ───────────────────────────────────────────────────

interface UsageResult { model: string; inputTokens: number; outputTokens: number }
type UsageExtractor = (args: unknown[], response: unknown) => UsageResult;
type TraceExtractor = (args: unknown[], response: unknown) => { provider: string; promptPreview: string; responsePreview: string };
type ChunkHandler = (state: StreamState, chunk: unknown) => void;

// OpenAI

const openaiUsage: UsageExtractor = (_args, response) => {
  const r = response as Record<string, unknown>;
  const usage = r?.usage as Record<string, number> | undefined;
  return {
    model: (r?.model as string) ?? "unknown",
    inputTokens: usage?.prompt_tokens ?? 0,
    outputTokens: usage?.completion_tokens ?? 0,
  };
};

const openaiTrace: TraceExtractor = (args, response) => {
  const r = response as Record<string, unknown>;
  const choices = (r?.choices as Array<Record<string, unknown>>) ?? [];
  const msg = (choices[0]?.message as Record<string, unknown>) ?? {};
  return {
    provider: "openai",
    promptPreview: lastMessageContent(args),
    responsePreview: String(msg?.content ?? "").slice(0, MAX_PREVIEW),
  };
};

const openaiChunk: ChunkHandler = (state, chunk) => {
  const c = chunk as Record<string, unknown>;
  const usage = c?.usage as Record<string, number> | undefined;
  if (usage) {
    state.inputTokens = usage.prompt_tokens ?? 0;
    state.outputTokens = usage.completion_tokens ?? 0;
  }
  if (c?.model) state.model = c.model as string;
  if (!state.responsePreview) {
    const choices = (c?.choices as Array<Record<string, unknown>>) ?? [];
    const text = safeGet(choices[0], "delta", "content");
    if (text) state.responsePreview = String(text).slice(0, MAX_PREVIEW);
  }
};

// Anthropic

const anthropicUsage: UsageExtractor = (_args, response) => {
  const r = response as Record<string, unknown>;
  const usage = r?.usage as Record<string, number> | undefined;
  return {
    model: (r?.model as string) ?? "unknown",
    inputTokens: usage?.input_tokens ?? 0,
    outputTokens: usage?.output_tokens ?? 0,
  };
};

const anthropicTrace: TraceExtractor = (args, response) => {
  const r = response as Record<string, unknown>;
  const content = (r?.content ?? []) as Array<Record<string, unknown>>;
  const text = content.find?.(b => b.type === "text");
  return {
    provider: "anthropic",
    promptPreview: lastMessageContent(args),
    responsePreview: String(text?.text ?? "").slice(0, MAX_PREVIEW),
  };
};

const anthropicChunk: ChunkHandler = (state, chunk) => {
  const c = chunk as Record<string, unknown>;
  const type = c?.type as string;
  if (type === "message_start") {
    const msg = c?.message as Record<string, unknown>;
    const usage = msg?.usage as Record<string, number> | undefined;
    if (usage) state.inputTokens = usage.input_tokens ?? 0;
    if (msg?.model) state.model = msg.model as string;
  } else if (type === "message_delta") {
    const usage = c?.usage as Record<string, number> | undefined;
    if (usage) state.outputTokens = usage.output_tokens ?? 0;
  } else if (type === "content_block_delta" && !state.responsePreview) {
    const text = safeGet(c, "delta", "text");
    if (text) state.responsePreview = String(text).slice(0, MAX_PREVIEW);
  }
};

// Google

const googleUsage: UsageExtractor = (args, response) => {
  const r = response as Record<string, unknown>;
  const usage = r?.usageMetadata as Record<string, number> | undefined;
  const body = (args[0] ?? {}) as Record<string, unknown>;
  let model = (body.model as string) ?? "gemini-unknown";
  if (model.includes("/")) model = model.split("/").pop()!;
  return {
    model,
    inputTokens: usage?.promptTokenCount ?? 0,
    outputTokens: usage?.candidatesTokenCount ?? 0,
  };
};

const googleTrace: TraceExtractor = (args, response) => {
  const body = (args[0] ?? {}) as Record<string, unknown>;
  const r = response as Record<string, unknown>;
  return {
    provider: "google",
    promptPreview: typeof body.contents === "string" ? body.contents.slice(0, MAX_PREVIEW) : "",
    responsePreview: String(r?.text ?? "").slice(0, MAX_PREVIEW),
  };
};

const googleChunk: ChunkHandler = (state, chunk) => {
  const c = chunk as Record<string, unknown>;
  const usage = c?.usageMetadata as Record<string, number> | undefined;
  if (usage) {
    state.inputTokens = usage.promptTokenCount ?? 0;
    state.outputTokens = usage.candidatesTokenCount ?? 0;
  }
  if (!state.responsePreview) {
    try { state.responsePreview = String((c as { text?: string }).text ?? "").slice(0, MAX_PREVIEW); } catch { /* ok */ }
  }
};

// ── Streaming ─────────────────────────────────────────────────────────────

interface StreamState {
  provider: string;
  model: string;
  promptPreview: string;
  startTime: number;
  inputTokens: number;
  outputTokens: number;
  responsePreview: string;
  recorded: boolean;
}

function finalizeStream(state: StreamState): void {
  if (state.recorded) return;
  state.recorded = true;
  const latencyMs = performance.now() - state.startTime;
  const cost = calculateCost(state.model, state.inputTokens, state.outputTokens);
  const trace = getActiveTrace();
  if (trace) {
    trace.addLlmCall({
      model: state.model, provider: state.provider,
      inputTokens: state.inputTokens, outputTokens: state.outputTokens, cost,
      latencyMs: Math.round(latencyMs * 10) / 10,
      promptPreview: state.promptPreview, responsePreview: state.responsePreview,
    });
  }
  getActiveTracker()?.recordCost(cost);
}

function wrapAsyncStream(
  stream: AsyncIterable<unknown>, state: StreamState, handler: ChunkHandler,
): AsyncIterable<unknown> {
  const iter = stream[Symbol.asyncIterator]();
  const wrapped: AsyncIterableIterator<unknown> = {
    [Symbol.asyncIterator]() { return wrapped; },
    async next() {
      let result: IteratorResult<unknown>;
      try {
        result = await iter.next();
      } catch (err) {
        finalizeStream(state);
        throw err;
      }
      if (result.done) finalizeStream(state);
      else handler(state, result.value);
      return result;
    },
    async return(value?: unknown) {
      finalizeStream(state);
      return iter.return?.(value) ?? { done: true as const, value: undefined };
    },
  };
  return new Proxy(wrapped, {
    get(target, prop) {
      if (prop in target) return (target as unknown as Record<string | symbol, unknown>)[prop];
      return (stream as unknown as Record<string | symbol, unknown>)[prop];
    },
  });
}

function isAsyncIterable(v: unknown): v is AsyncIterable<unknown> {
  return v != null && typeof (v as Record<symbol, unknown>)[Symbol.asyncIterator] === "function";
}

// ── Generic patcher ───────────────────────────────────────────────────────

interface ProviderConfig {
  name: string;
  usage: UsageExtractor;
  trace: TraceExtractor;
  chunk: ChunkHandler;
  preRequest?: (body: Record<string, unknown>) => void;
}

function makeWrapper(
  original: (...args: unknown[]) => Promise<unknown>,
  config: ProviderConfig,
): (...args: unknown[]) => Promise<unknown> {
  const fn = async function (this: unknown, ...args: unknown[]) {
    getActiveTracker()?.preCallCheck();

    const body = (args[0] ?? {}) as Record<string, unknown>;
    config.preRequest?.(body);

    const start = performance.now();
    const response = await original.apply(this, args);

    if (body.stream && isAsyncIterable(response)) {
      const { provider, promptPreview } = config.trace(args, response);
      const state: StreamState = {
        provider, model: (body.model as string) ?? "unknown",
        promptPreview, startTime: start,
        inputTokens: 0, outputTokens: 0, responsePreview: "", recorded: false,
      };
      return wrapAsyncStream(response, state, config.chunk);
    }

    const latencyMs = performance.now() - start;
    const { model, inputTokens, outputTokens } = config.usage(args, response);
    const cost = calculateCost(model, inputTokens, outputTokens);
    const trace = getActiveTrace();
    if (trace) {
      const { provider, promptPreview, responsePreview } = config.trace(args, response);
      trace.addLlmCall({
        model, provider, inputTokens, outputTokens, cost,
        latencyMs: Math.round(latencyMs * 10) / 10,
        promptPreview, responsePreview,
      });
    }
    getActiveTracker()?.recordCost(cost);
    return response;
  };
  (fn as unknown as Record<string, unknown>)[SENTINEL] = true;
  return fn;
}

// ── Patch state ───────────────────────────────────────────────────────────

interface PatchProviderStatus {
  installed: boolean;
  patched: boolean;
  verified: boolean;
  error: string | null;
}

const _patchStatus: Record<string, PatchProviderStatus> = {
  openai: { installed: false, patched: false, verified: false, error: null },
  anthropic: { installed: false, patched: false, verified: false, error: null },
  google: { installed: false, patched: false, verified: false, error: null },
};

const _patched: Record<string, boolean> = { openai: false, anthropic: false, google: false };

// ── Resolvers & verifiers ─────────────────────────────────────────────────

type Target = { owner: { prototype: Record<string, unknown> }; attr: string } | null;

function resolveOpenAI(): Target {
  try {
    const openai = require("openai");
    // openai.OpenAI.Chat.Completions is the actual class used by OpenAI instances.
    // openai.OpenAI.Completions exists but is a different class (not used at runtime).
    const cls = openai?.OpenAI?.Chat?.Completions ?? openai?.resources?.chat?.completions?.Completions;
    return cls?.prototype?.create ? { owner: cls, attr: "create" } : null;
  } catch { return null; }
}

function resolveAnthropic(): Target {
  try {
    const sdk = require("@anthropic-ai/sdk");
    const cls = sdk?.resources?.messages?.Messages
      ?? sdk?.Anthropic?.Messages ?? sdk?.default?.Messages;
    return cls?.prototype?.create ? { owner: cls, attr: "create" } : null;
  } catch { return null; }
}

function resolveGoogle(): Target {
  try {
    const genai = require("@google/genai");
    const cls = genai?.Models;
    // generateContent is set as an own property on each instance in the constructor;
    // generateContentInternal is on the prototype and is what generateContent delegates to.
    return cls?.prototype?.generateContentInternal ? { owner: cls, attr: "generateContentInternal" } : null;
  } catch { return null; }
}

function verify(resolve: () => Target): boolean {
  const target = resolve();
  if (!target) return false;
  return !!(target.owner.prototype[target.attr] as Record<string, unknown>)?.[SENTINEL];
}

// ── Provider configs ──────────────────────────────────────────────────────

const openaiConfig: ProviderConfig = {
  name: "openai", usage: openaiUsage, trace: openaiTrace, chunk: openaiChunk,
  preRequest(body) {
    if (body.stream) {
      const opts = (body.stream_options ?? {}) as Record<string, unknown>;
      if (!opts.include_usage) body.stream_options = { ...opts, include_usage: true };
    }
  },
};

const anthropicConfig: ProviderConfig = {
  name: "anthropic", usage: anthropicUsage, trace: anthropicTrace, chunk: anthropicChunk,
};

const googleConfig: ProviderConfig = {
  name: "google", usage: googleUsage, trace: googleTrace, chunk: googleChunk,
};

// ── Apply patches ─────────────────────────────────────────────────────────

function applyPatch(name: string, resolve: () => Target, config: ProviderConfig, verifier: () => boolean): void {
  if (_patched[name]) return;

  try {
    if (name === "openai") require("openai");
    else if (name === "anthropic") require("@anthropic-ai/sdk");
    else if (name === "google") require("@google/genai");
    _patchStatus[name].installed = true;
  } catch { return; }

  try {
    const target = resolve();
    if (!target) return;

    target.owner.prototype[target.attr] = makeWrapper(
      target.owner.prototype[target.attr] as (...args: unknown[]) => Promise<unknown>,
      config,
    );
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    _patchStatus[name].error = msg;
    if (process.env.WICKD_DEBUG) {
      process.stderr.write(`[wickd] Failed to patch ${name}: ${msg}\n`);
    }
    return;
  }

  _patched[name] = true;
  _patchStatus[name].patched = true;
  _patchStatus[name].verified = verifier();
  if (process.env.WICKD_DEBUG) {
    process.stderr.write(`[wickd] Patched ${name}\n`);
  }
}

// ── Public API ────────────────────────────────────────────────────────────

export function patchOpenAI(): void {
  applyPatch("openai", resolveOpenAI, openaiConfig, () => verify(resolveOpenAI));
}

export function patchAnthropic(): void {
  applyPatch("anthropic", resolveAnthropic, anthropicConfig, () => verify(resolveAnthropic));
}

export function patchGoogle(): void {
  applyPatch("google", resolveGoogle, googleConfig, () => verify(resolveGoogle));
}

export function patchAll(): void {
  patchOpenAI();
  patchAnthropic();
  patchGoogle();
  // Auto-track MCP tool calls when the SDK is installed.
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { patchMcp } = require("./mcpPatch.js") as typeof import("./mcpPatch.js");
    patchMcp();
  } catch {
    /* MCP module missing — safe to ignore. */
  }
}

export function patchStatus(): Record<string, PatchProviderStatus> {
  return Object.fromEntries(Object.entries(_patchStatus).map(([k, v]) => [k, { ...v }]));
}

export function verifyPatches(): Record<string, PatchProviderStatus> {
  const verifiers: Record<string, () => boolean> = {
    openai: () => verify(resolveOpenAI),
    anthropic: () => verify(resolveAnthropic),
    google: () => verify(resolveGoogle),
  };
  for (const [name, fn] of Object.entries(verifiers)) {
    if (_patchStatus[name].patched) _patchStatus[name].verified = fn();
  }
  const status = patchStatus();
  // Surface MCP status when available.
  try {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { mcpPatchStatus, verifyMcpPatch } = require("./mcpPatch.js") as typeof import("./mcpPatch.js");
    verifyMcpPatch();
    const mcp = mcpPatchStatus();
    if (mcp.installed) {
      (status as Record<string, PatchProviderStatus>).mcp = mcp as unknown as PatchProviderStatus;
    }
  } catch {
    /* ignore */
  }
  return status;
}

export function healthStatus(): { patches: Record<string, PatchProviderStatus>; sdkVersions: Record<string, string> } {
  const sdkVersions: Record<string, string> = {};
  for (const [name, pkg] of [["openai", "openai"], ["anthropic", "@anthropic-ai/sdk"], ["google", "@google/genai"]] as const) {
    try {
      const mod = require(pkg);
      sdkVersions[name] = mod?.VERSION ?? mod?.version ?? "unknown";
    } catch { /* not installed */ }
  }
  return { patches: verifyPatches(), sdkVersions };
}
