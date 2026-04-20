import type { Context } from "hono";

import { forwardHeaders, type ProviderContext } from "./types.js";

interface ChatRequestBody {
  readonly model?: unknown;
  readonly stream?: unknown;
}

interface OpenAIUsage {
  readonly prompt_tokens?: number;
  readonly completion_tokens?: number;
}

interface ChatResponseBody {
  readonly usage?: OpenAIUsage;
}

/**
 * Proxies `POST /v1/chat/completions` (non-streaming).
 *
 * Streaming (`stream: true`) returns 501 and will be handled in a follow-up
 * commit that adds SSE tee + usage extraction from the final chunk.
 */
export async function openaiChatCompletions(
  c: Context,
  providerCtx: ProviderContext,
): Promise<Response> {
  const rawBody = await c.req.text();
  const parsed = parseJson(rawBody);
  if (parsed === null) {
    return c.json({ error: { message: "Invalid JSON body", type: "invalid_request" } }, 400);
  }

  const body = parsed as ChatRequestBody;
  if (body.stream === true) {
    return c.json(
      {
        error: {
          message: "Streaming is not yet supported by the Wickd proxy.",
          type: "not_implemented",
        },
      },
      501,
    );
  }

  const model = typeof body.model === "string" ? body.model : "unknown";
  const run = providerCtx.runTracker.attach(
    c.req.header("x-wickd-run-id") ?? null,
  );

  const span = providerCtx.store.startSpan({
    runId: run.id,
    kind: "llm",
    name: model,
    provider: "openai",
    model,
    input: parsed,
  });

  const upstreamUrl = joinUrl(providerCtx.upstreamBaseUrl, "/v1/chat/completions");
  const headers = forwardHeaders(c.req.raw.headers);

  let upstream: Response;
  try {
    upstream = await providerCtx.fetch(upstreamUrl, {
      method: "POST",
      headers,
      body: rawBody,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "upstream request failed";
    providerCtx.store.endSpan(span.id, {
      status: "error",
      error: message,
    });
    return c.json(
      { error: { message: `Wickd proxy: upstream unreachable (${message})`, type: "bad_gateway" } },
      502,
    );
  }

  const responseText = await upstream.text();
  const responseBody = parseJson(responseText);

  if (!upstream.ok) {
    providerCtx.store.endSpan(span.id, {
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
  providerCtx.store.endSpan(span.id, {
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

function extractUsage(
  body: Record<string, unknown> | null,
): { tokensInput: number | null; tokensOutput: number | null } {
  if (body === null) return { tokensInput: null, tokensOutput: null };
  const usage = (body as ChatResponseBody).usage;
  if (usage === undefined) return { tokensInput: null, tokensOutput: null };
  return {
    tokensInput: typeof usage.prompt_tokens === "number" ? usage.prompt_tokens : null,
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
    if (lower === "content-encoding" || lower === "content-length") return;
    if (lower === "transfer-encoding" || lower === "connection") return;
    out.set(key, value);
  });
  return out;
}
