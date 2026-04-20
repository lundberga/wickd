import type { SseEvent } from "../streaming/sse.js";

import {
  createProviderRoute,
  joinUrl,
  type ProviderAdapter,
  type StreamUsage,
} from "./adapter.js";
import type { ProviderHandler } from "./types.js";

const UPSTREAM_PATH = "/v1/messages";

/**
 * Anthropic reports usage across two event types:
 *  - `message_start`: initial `input_tokens` and a provisional `output_tokens`
 *  - `message_delta`: cumulative `output_tokens` updates
 * Both events are JSON payloads carried on separate SSE events.
 */
interface AnthropicUsage {
  readonly input_tokens?: number;
  readonly output_tokens?: number;
}

interface MessageStartData {
  readonly type?: string;
  readonly message?: { readonly usage?: AnthropicUsage };
}

interface MessageDeltaData {
  readonly type?: string;
  readonly usage?: AnthropicUsage;
}

export const anthropicAdapter: ProviderAdapter = {
  name: "anthropic",

  upstreamUrl: (_req, base) => joinUrl(base, UPSTREAM_PATH),

  model: (req) =>
    typeof req.body["model"] === "string" ? (req.body["model"] as string) : "unknown",

  isStreaming: (req) => req.body["stream"] === true,

  extractUsage: (body) => extractAnthropicUsage(body),

  parseStreamEvent: (event) => parseAnthropicStreamEvent(event),
};

export const anthropicMessages: ProviderHandler = createProviderRoute(anthropicAdapter);

function extractAnthropicUsage(
  body: Record<string, unknown> | null,
): StreamUsage {
  if (body === null) return { tokensInput: null, tokensOutput: null };
  const usage = (body as { usage?: AnthropicUsage }).usage;
  if (usage === undefined) return { tokensInput: null, tokensOutput: null };
  return {
    tokensInput:
      typeof usage.input_tokens === "number" ? usage.input_tokens : null,
    tokensOutput:
      typeof usage.output_tokens === "number" ? usage.output_tokens : null,
  };
}

function parseAnthropicStreamEvent(event: SseEvent): StreamUsage | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(event.data);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object") return null;

  const type =
    (parsed as { type?: unknown }).type ?? event.event ?? "";

  if (type === "message_start") {
    const usage = (parsed as MessageStartData).message?.usage;
    if (usage === undefined) return null;
    return {
      tokensInput:
        typeof usage.input_tokens === "number" ? usage.input_tokens : null,
      tokensOutput:
        typeof usage.output_tokens === "number" ? usage.output_tokens : null,
    };
  }

  if (type === "message_delta") {
    const usage = (parsed as MessageDeltaData).usage;
    if (usage === undefined) return null;
    return {
      tokensInput:
        typeof usage.input_tokens === "number" ? usage.input_tokens : null,
      tokensOutput:
        typeof usage.output_tokens === "number" ? usage.output_tokens : null,
    };
  }

  return null;
}
