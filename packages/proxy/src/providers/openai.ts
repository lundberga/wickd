import type { Context } from "hono";

import { SseParser } from "../streaming/sse.js";
import {
  forwardHeaders,
  type ProviderContext,
} from "./types.js";

interface ChatRequestBody {
  readonly model?: unknown;
  readonly stream?: unknown;
  readonly stream_options?: unknown;
}

interface OpenAIUsage {
  readonly prompt_tokens?: number;
  readonly completion_tokens?: number;
}

interface ChatResponseBody {
  readonly usage?: OpenAIUsage;
}

const UPSTREAM_PATH = "/v1/chat/completions";
const PROVIDER = "openai";
const DONE_SENTINEL = "[DONE]";

/**
 * Proxies `POST /v1/chat/completions` for both streaming and non-streaming
 * requests. Streaming transparently pipes upstream bytes to the client and
 * extracts token usage from the final chunk for cost attribution.
 */
export async function openaiChatCompletions(
  c: Context,
  providerCtx: ProviderContext,
): Promise<Response> {
  const rawBody = await c.req.text();
  const parsed = parseJson(rawBody);
  if (parsed === null) {
    return c.json(
      { error: { message: "Invalid JSON body", type: "invalid_request" } },
      400,
    );
  }

  const body = parsed as ChatRequestBody;
  const model = typeof body.model === "string" ? body.model : "unknown";
  const run = providerCtx.runTracker.attach(
    c.req.header("x-wickd-run-id") ?? null,
  );
  const span = providerCtx.store.startSpan({
    runId: run.id,
    kind: "llm",
    name: model,
    provider: PROVIDER,
    model,
    input: parsed,
  });

  if (body.stream === true) {
    return handleStreaming(c, providerCtx, parsed, span.id);
  }
  return handleNonStreaming(c, providerCtx, rawBody, span.id);
}

// --- non-streaming --------------------------------------------------------

async function handleNonStreaming(
  c: Context,
  providerCtx: ProviderContext,
  rawBody: string,
  spanId: string,
): Promise<Response> {
  const upstreamUrl = joinUrl(providerCtx.upstreamBaseUrl, UPSTREAM_PATH);
  const headers = forwardHeaders(c.req.raw.headers);

  let upstream: Response;
  try {
    upstream = await providerCtx.fetch(upstreamUrl, {
      method: "POST",
      headers,
      body: rawBody,
    });
  } catch (err) {
    return handleUpstreamFailure(c, providerCtx, spanId, err);
  }

  const responseText = await upstream.text();
  const responseBody = parseJson(responseText);

  if (!upstream.ok) {
    providerCtx.store.endSpan(spanId, {
      status: "error",
      error: `upstream status ${upstream.status}`,
      ...(responseBody !== null ? { output: responseBody } : {}),
    });
    return new Response(responseText, {
      status: upstream.status,
      headers: passThroughHeaders(upstream.headers),
    });
  }

  const { tokensInput, tokensOutput } = extractUsage(responseBody);
  providerCtx.store.endSpan(spanId, {
    status: "ok",
    tokensInput,
    tokensOutput,
    ...(responseBody !== null ? { output: responseBody } : {}),
  });

  return new Response(responseText, {
    status: upstream.status,
    headers: passThroughHeaders(upstream.headers),
  });
}

// --- streaming ------------------------------------------------------------

async function handleStreaming(
  c: Context,
  providerCtx: ProviderContext,
  body: Record<string, unknown>,
  spanId: string,
): Promise<Response> {
  const mutatedBody = withIncludeUsage(body);
  const upstreamUrl = joinUrl(providerCtx.upstreamBaseUrl, UPSTREAM_PATH);
  const headers = forwardHeaders(c.req.raw.headers);

  let upstream: Response;
  try {
    upstream = await providerCtx.fetch(upstreamUrl, {
      method: "POST",
      headers,
      body: JSON.stringify(mutatedBody),
    });
  } catch (err) {
    return handleUpstreamFailure(c, providerCtx, spanId, err);
  }

  if (!upstream.ok || upstream.body === null) {
    const errorText = await upstream.text();
    providerCtx.store.endSpan(spanId, {
      status: "error",
      error: `upstream status ${upstream.status}`,
    });
    return new Response(errorText, {
      status: upstream.status,
      headers: passThroughHeaders(upstream.headers),
    });
  }

  // tee() gives us two independent readers. The client consumes one; we
  // parse the other in the background to extract token usage. Either side
  // can lag; ReadableStream.tee buffers internally.
  const [clientLeg, parserLeg] = upstream.body.tee();

  // Fire-and-forget background task. collectUsage always terminates the
  // span, even on error, and never re-throws, so no unhandled rejection.
  void collectUsage(parserLeg, providerCtx, spanId);

  return new Response(clientLeg, {
    status: upstream.status,
    headers: passThroughHeaders(upstream.headers),
  });
}

async function collectUsage(
  stream: ReadableStream<Uint8Array>,
  providerCtx: ProviderContext,
  spanId: string,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  let usage: OpenAIUsage | null = null;
  let streamError: string | null = null;

  try {
    let done = false;
    while (!done) {
      const result = await reader.read();
      done = result.done;
      if (result.value !== undefined) {
        const chunk = decoder.decode(result.value, { stream: !done });
        for (const event of parser.push(chunk)) {
          const found = extractUsageFromEvent(event.data);
          if (found !== null) usage = found;
        }
      }
    }
    for (const event of parser.flush()) {
      const found = extractUsageFromEvent(event.data);
      if (found !== null) usage = found;
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
      tokensInput: usage?.prompt_tokens ?? null,
      tokensOutput: usage?.completion_tokens ?? null,
    });
  } catch {
    // Span was already ended (e.g., test tore down Store). Safe to ignore —
    // the stream is already finished, nothing else observes this failure.
  }
}

function extractUsageFromEvent(data: string): OpenAIUsage | null {
  if (data === DONE_SENTINEL) return null;
  try {
    const parsed = JSON.parse(data) as { usage?: OpenAIUsage };
    return parsed.usage ?? null;
  } catch {
    return null;
  }
}

function withIncludeUsage(
  body: Record<string, unknown>,
): Record<string, unknown> {
  const existing = body["stream_options"];
  const streamOptions =
    existing !== null && typeof existing === "object" && !Array.isArray(existing)
      ? (existing as Record<string, unknown>)
      : {};
  return {
    ...body,
    stream_options: { ...streamOptions, include_usage: true },
  };
}

// --- shared helpers -------------------------------------------------------

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

function extractUsage(
  body: Record<string, unknown> | null,
): { tokensInput: number | null; tokensOutput: number | null } {
  if (body === null) return { tokensInput: null, tokensOutput: null };
  const usage = (body as ChatResponseBody).usage;
  if (usage === undefined) return { tokensInput: null, tokensOutput: null };
  return {
    tokensInput:
      typeof usage.prompt_tokens === "number" ? usage.prompt_tokens : null,
    tokensOutput:
      typeof usage.completion_tokens === "number" ? usage.completion_tokens : null,
  };
}

function parseJson(raw: string): Record<string, unknown> | null {
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

function joinUrl(base: string, path: string): string {
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
