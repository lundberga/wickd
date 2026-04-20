import type { SseEvent } from "../streaming/sse.js";

import {
  createProviderRoute,
  joinUrl,
  type ProviderAdapter,
  type RequestInfo,
  type StreamUsage,
} from "./adapter.js";
import type { ProviderHandler } from "./types.js";

const PROXY_PREFIX = "/google";

/**
 * Google Gemini's request shape differs from OpenAI/Anthropic:
 *  - Model is encoded in the path (`/v1beta/models/<model>:generateContent`),
 *    not in the JSON body.
 *  - Streaming is selected by the method suffix (`:streamGenerateContent`)
 *    rather than a `stream: true` body field. With `?alt=sse`, streaming
 *    responses are SSE chunks; `usageMetadata` is emitted cumulatively.
 */
interface GoogleUsageMetadata {
  readonly promptTokenCount?: number;
  readonly candidatesTokenCount?: number;
}

interface PathParts {
  readonly model: string;
  readonly method: string;
}

export const googleAdapter: ProviderAdapter = {
  name: "google",

  upstreamUrl: (req, base) => {
    // Proxy path: /google/v1beta/models/<model>:<method>
    // Upstream:   <base>/v1beta/models/<model>:<method>[?querystring]
    const suffix = req.path.startsWith(PROXY_PREFIX)
      ? req.path.slice(PROXY_PREFIX.length)
      : req.path;
    return `${joinUrl(base, suffix)}${req.querystring}`;
  },

  model: (req) => parsePath(req.path).model,

  isStreaming: (req) => parsePath(req.path).method === "streamGenerateContent",

  extractUsage: (body) => extractGoogleUsage(body),

  parseStreamEvent: (event) => parseGoogleStreamEvent(event),
};

export const googleGenerateContent: ProviderHandler = createProviderRoute(googleAdapter);

function parsePath(path: string): PathParts {
  // Strip the leading /google, if present.
  const rest = path.startsWith(PROXY_PREFIX) ? path.slice(PROXY_PREFIX.length) : path;
  // Expected shape: /v1beta/models/<model>:<method>
  const match = /\/models\/([^/]+?):([^/?]+)(?:$|\?)/.exec(rest);
  if (match === null || match[1] === undefined || match[2] === undefined) {
    return { model: "unknown", method: "" };
  }
  return { model: match[1], method: match[2] };
}

function extractGoogleUsage(
  body: Record<string, unknown> | null,
): StreamUsage {
  if (body === null) return { tokensInput: null, tokensOutput: null };
  const usage = (body as { usageMetadata?: GoogleUsageMetadata }).usageMetadata;
  if (usage === undefined) return { tokensInput: null, tokensOutput: null };
  return {
    tokensInput:
      typeof usage.promptTokenCount === "number" ? usage.promptTokenCount : null,
    tokensOutput:
      typeof usage.candidatesTokenCount === "number"
        ? usage.candidatesTokenCount
        : null,
  };
}

function parseGoogleStreamEvent(event: SseEvent): StreamUsage | null {
  try {
    const parsed = JSON.parse(event.data) as {
      usageMetadata?: GoogleUsageMetadata;
    };
    if (parsed.usageMetadata === undefined) return null;
    return {
      tokensInput:
        typeof parsed.usageMetadata.promptTokenCount === "number"
          ? parsed.usageMetadata.promptTokenCount
          : null,
      tokensOutput:
        typeof parsed.usageMetadata.candidatesTokenCount === "number"
          ? parsed.usageMetadata.candidatesTokenCount
          : null,
    };
  } catch {
    return null;
  }
}
