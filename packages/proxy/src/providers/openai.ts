import type { SseEvent } from "../streaming/sse.js";

import {
  createProviderRoute,
  joinUrl,
  type ProviderAdapter,
  type RequestInfo,
  type StreamUsage,
} from "./adapter.js";
import type { ProviderHandler } from "./types.js";

const UPSTREAM_PATH = "/v1/chat/completions";
const DONE_SENTINEL = "[DONE]";

interface OpenAIUsage {
  readonly prompt_tokens?: number;
  readonly completion_tokens?: number;
}

export const openaiAdapter: ProviderAdapter = {
  name: "openai",

  upstreamUrl: (_req, base) => joinUrl(base, UPSTREAM_PATH),

  model: (req) =>
    typeof req.body["model"] === "string" ? (req.body["model"] as string) : "unknown",

  isStreaming: (req) => req.body["stream"] === true,

  prepareStreamingBody: (req) => withIncludeUsage(req.body),

  extractUsage: (body) => extractOpenAIUsage(body),

  parseStreamEvent: (event) => parseOpenAIStreamEvent(event),
};

export const openaiChatCompletions: ProviderHandler = createProviderRoute(openaiAdapter);

function extractOpenAIUsage(
  body: Record<string, unknown> | null,
): StreamUsage {
  if (body === null) return { tokensInput: null, tokensOutput: null };
  const usage = (body as { usage?: OpenAIUsage }).usage;
  if (usage === undefined) return { tokensInput: null, tokensOutput: null };
  return {
    tokensInput:
      typeof usage.prompt_tokens === "number" ? usage.prompt_tokens : null,
    tokensOutput:
      typeof usage.completion_tokens === "number" ? usage.completion_tokens : null,
  };
}

function parseOpenAIStreamEvent(event: SseEvent): StreamUsage | null {
  if (event.data === DONE_SENTINEL) return null;
  try {
    const parsed = JSON.parse(event.data) as { usage?: OpenAIUsage };
    if (parsed.usage === undefined) return null;
    return {
      tokensInput:
        typeof parsed.usage.prompt_tokens === "number"
          ? parsed.usage.prompt_tokens
          : null,
      tokensOutput:
        typeof parsed.usage.completion_tokens === "number"
          ? parsed.usage.completion_tokens
          : null,
    };
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
