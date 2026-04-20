import type { Context } from "hono";
import type { Store } from "wickd-core";

import type { RunTracker } from "../runs.js";

export interface ProviderContext {
  readonly store: Store;
  readonly runTracker: RunTracker;
  readonly upstreamBaseUrl: string;
  readonly fetch: typeof globalThis.fetch;
}

export type ProviderHandler = (
  c: Context,
  providerCtx: ProviderContext,
) => Promise<Response>;

/**
 * Hop-by-hop headers (RFC 7230 §6.1) plus headers that are specific to the
 * inbound request and must not be forwarded. `host` is bound to the client
 * side; `content-length` is recomputed by fetch when we re-serialize.
 */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

export function forwardHeaders(incoming: Headers): Headers {
  const out = new Headers();
  incoming.forEach((value, key) => {
    if (HOP_BY_HOP.has(key.toLowerCase())) return;
    out.set(key, value);
  });
  return out;
}
