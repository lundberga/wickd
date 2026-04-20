import { Hono } from "hono";
import type { Store } from "wickd-core";

import type { ProxyConfig } from "./config.js";
import { openaiChatCompletions } from "./providers/openai.js";
import type { ProviderContext } from "./providers/types.js";
import { RunTracker } from "./runs.js";

export interface ProxyAppDeps {
  readonly store: Store;
  readonly config: ProxyConfig;
  /**
   * Injectable fetch for tests. Defaults to the global `fetch`.
   */
  readonly fetch?: typeof globalThis.fetch;
}

export interface ProxyApp {
  readonly app: Hono;
  readonly runTracker: RunTracker;
  /**
   * Gracefully end the current session run. Safe to call multiple times.
   */
  shutdown(): void;
}

export function createProxy(deps: ProxyAppDeps): ProxyApp {
  const runTracker = new RunTracker(deps.store, {
    runName: deps.config.runName,
    idleTimeoutMs: deps.config.idleTimeoutMs,
  });
  const fetchFn = deps.fetch ?? globalThis.fetch;

  const openai: ProviderContext = {
    store: deps.store,
    runTracker,
    upstreamBaseUrl: deps.config.upstream.openai,
    fetch: fetchFn,
  };

  const app = new Hono();

  app.get("/health", (c) => c.json({ ok: true }));

  app.post("/openai/v1/chat/completions", (c) => openaiChatCompletions(c, openai));

  return {
    app,
    runTracker,
    shutdown: () => runTracker.shutdown(),
  };
}
