import type { Context } from "hono";

import { SseParser, type SseEvent } from "../streaming/sse.js";

import {
  forwardHeaders,
  type ProviderContext,
  type ProviderHandler,
} from "./types.js";

export interface RequestInfo {
  /** Proxy-side path, e.g. `/openai/v1/chat/completions`. */
  readonly path: string;
  /** Raw query string starting with `?`, or empty string. */
  readonly querystring: string;
  /** Parsed JSON body. */
  readonly body: Record<string, unknown>;
  /** Raw body text, suitable for forwarding unchanged. */
  readonly rawBody: string;
}

export interface StreamUsage {
  readonly tokensInput: number | null;
  readonly tokensOutput: number | null;
}

/**
 * Provider-specific contract. Each adapter describes how to talk to one LLM
 * vendor's HTTP API; the shared handlers in this module drive the lifecycle.
 */
export interface ProviderAdapter {
  readonly name: string;

  /** Absolute upstream URL for the forwarded request. */
  upstreamUrl(req: RequestInfo, base: string): string;

  /** Model identifier for span attribution. Returns `"unknown"` if indeterminate. */
  model(req: RequestInfo): string;

  /** `true` when the incoming request asks for a streaming response. */
  isStreaming(req: RequestInfo): boolean;

  /**
   * Optional: transform the body before forwarding a streaming request.
   * Defaults to the identity function. Used, e.g., to inject
   * `stream_options.include_usage` for OpenAI so we can extract tokens.
   */
  prepareStreamingBody?(req: RequestInfo): Record<string, unknown>;

  /** Extract token counts from a parsed non-streaming response body. */
  extractUsage(body: Record<string, unknown> | null): StreamUsage;

  /**
   * Parse a single SSE event from the streaming response. Return any usage
   * update the event contributes, or `null` if the event has no usage.
   * Partial updates (one field null, the other set) are merged by the
   * shared handler by keeping the most recent non-null value per field.
   */
  parseStreamEvent(event: SseEvent): StreamUsage | null;
}

const EMPTY_USAGE: StreamUsage = { tokensInput: null, tokensOutput: null };

export function createProviderRoute(adapter: ProviderAdapter): ProviderHandler {
  return async (c, providerCtx) => {
    const rawBody = await c.req.text();
    const parsed = parseJson(rawBody);
    if (parsed === null) {
      return c.json(
        { error: { message: "Invalid JSON body", type: "invalid_request" } },
        400,
      );
    }

    const url = new URL(c.req.url);
    const req: RequestInfo = {
      path: url.pathname,
      querystring: url.search,
      body: parsed,
      rawBody,
    };

    const model = adapter.model(req);
    const run = providerCtx.runTracker.attach(
      c.req.header("x-wickd-run-id") ?? null,
    );
    const span = providerCtx.store.startSpan({
      runId: run.id,
      kind: "llm",
      name: model,
      provider: adapter.name,
      model,
      input: parsed,
    });

    const upstreamUrl = adapter.upstreamUrl(req, providerCtx.upstreamBaseUrl);

    if (adapter.isStreaming(req)) {
      const bodyToForward = adapter.prepareStreamingBody?.(req) ?? parsed;
      return handleStreaming(c, providerCtx, adapter, {
        spanId: span.id,
        upstreamUrl,
        rawBody: JSON.stringify(bodyToForward),
      });
    }
    return handleNonStreaming(c, providerCtx, adapter, {
      spanId: span.id,
      upstreamUrl,
      rawBody,
    });
  };
}

// --- shared handlers ------------------------------------------------------

interface ForwardTarget {
  readonly spanId: string;
  readonly upstreamUrl: string;
  readonly rawBody: string;
}

async function handleNonStreaming(
  c: Context,
  providerCtx: ProviderContext,
  adapter: ProviderAdapter,
  target: ForwardTarget,
): Promise<Response> {
  const headers = forwardHeaders(c.req.raw.headers);

  let upstream: Response;
  try {
    upstream = await providerCtx.fetch(target.upstreamUrl, {
      method: "POST",
      headers,
      body: target.rawBody,
    });
  } catch (err) {
    return handleUpstreamFailure(c, providerCtx, target.spanId, err);
  }

  const responseText = await upstream.text();
  const responseBody = parseJson(responseText);

  if (!upstream.ok) {
    providerCtx.store.endSpan(target.spanId, {
      status: "error",
      error: `upstream status ${upstream.status}`,
      ...(responseBody !== null ? { output: responseBody } : {}),
    });
    return new Response(responseText, {
      status: upstream.status,
      headers: passThroughHeaders(upstream.headers),
    });
  }

  const usage = adapter.extractUsage(responseBody);
  providerCtx.store.endSpan(target.spanId, {
    status: "ok",
    tokensInput: usage.tokensInput,
    tokensOutput: usage.tokensOutput,
    ...(responseBody !== null ? { output: responseBody } : {}),
  });

  return new Response(responseText, {
    status: upstream.status,
    headers: passThroughHeaders(upstream.headers),
  });
}

async function handleStreaming(
  c: Context,
  providerCtx: ProviderContext,
  adapter: ProviderAdapter,
  target: ForwardTarget,
): Promise<Response> {
  const headers = forwardHeaders(c.req.raw.headers);

  let upstream: Response;
  try {
    upstream = await providerCtx.fetch(target.upstreamUrl, {
      method: "POST",
      headers,
      body: target.rawBody,
    });
  } catch (err) {
    return handleUpstreamFailure(c, providerCtx, target.spanId, err);
  }

  if (!upstream.ok || upstream.body === null) {
    const errorText = await upstream.text();
    providerCtx.store.endSpan(target.spanId, {
      status: "error",
      error: `upstream status ${upstream.status}`,
    });
    return new Response(errorText, {
      status: upstream.status,
      headers: passThroughHeaders(upstream.headers),
    });
  }

  const [clientLeg, parserLeg] = upstream.body.tee();
  void collectStreamUsage(parserLeg, adapter, providerCtx, target.spanId);

  return new Response(clientLeg, {
    status: upstream.status,
    headers: passThroughHeaders(upstream.headers),
  });
}

async function collectStreamUsage(
  stream: ReadableStream<Uint8Array>,
  adapter: ProviderAdapter,
  providerCtx: ProviderContext,
  spanId: string,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let usage: StreamUsage = EMPTY_USAGE;
  let streamError: string | null = null;

  try {
    let done = false;
    while (!done) {
      const result = await reader.read();
      done = result.done;
      if (result.value !== undefined) {
        const chunk = decoder.decode(result.value, { stream: !done });
        for (const event of parser.push(chunk)) {
          const update = adapter.parseStreamEvent(event);
          if (update !== null) usage = mergeUsage(usage, update);
        }
      }
    }
    for (const event of parser.flush()) {
      const update = adapter.parseStreamEvent(event);
      if (update !== null) usage = mergeUsage(usage, update);
    }
  } catch (err) {
    streamError = err instanceof Error ? err.message : "stream error";
  } finally {
    reader.releaseLock();
  }

  try {
    if (streamError !== null) {
      providerCtx.store.endSpan(spanId, {
        status: "error",
        error: streamError,
      });
      return;
    }
    providerCtx.store.endSpan(spanId, {
      status: "ok",
      tokensInput: usage.tokensInput,
      tokensOutput: usage.tokensOutput,
    });
  } catch {
    // Span was already ended (e.g., test tore down Store). The stream is
    // done; no further observer will see this failure.
  }
}

function mergeUsage(current: StreamUsage, update: StreamUsage): StreamUsage {
  return {
    tokensInput: update.tokensInput ?? current.tokensInput,
    tokensOutput: update.tokensOutput ?? current.tokensOutput,
  };
}

function handleUpstreamFailure(
  c: Context,
  providerCtx: ProviderContext,
  spanId: string,
  err: unknown,
): Response {
  const message = err instanceof Error ? err.message : "upstream request failed";
  providerCtx.store.endSpan(spanId, {
    status: "error",
    error: message,
  });
  return c.json(
    {
      error: {
        message: `Wickd proxy: upstream unreachable (${message})`,
        type: "bad_gateway",
      },
    },
    502,
  );
}

// --- shared utilities -----------------------------------------------------

export function parseJson(raw: string): Record<string, unknown> | null {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function joinUrl(base: string, path: string): string {
  const trimmedBase = base.endsWith("/") ? base.slice(0, -1) : base;
  const trimmedPath = path.startsWith("/") ? path : `/${path}`;
  return `${trimmedBase}${trimmedPath}`;
}

function passThroughHeaders(upstream: Headers): Headers {
  const out = new Headers();
  upstream.forEach((value, key) => {
    const lower = key.toLowerCase();
    if (
      lower === "content-encoding" ||
      lower === "content-length" ||
      lower === "transfer-encoding" ||
      lower === "connection"
    ) {
      return;
    }
    out.set(key, value);
  });
  return out;
}
