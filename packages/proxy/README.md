# wickd-proxy

The Wickd proxy: a drop-in HTTP proxy that records every LLM and MCP call made by your agent, with zero code changes.

```bash
# Not wired up yet — CLI arrives in a later sub-commit.
wickd start
```

Once running, point your SDK at it:

```bash
export OPENAI_BASE_URL="http://127.0.0.1:4319/openai/v1"
```

Your agent code does not change. The proxy forwards the request to the real OpenAI API, records a span with token counts and cost, and returns the upstream response unmodified.

## Status

This package is landing in sub-commits. Track what is and isn't wired here:

| Feature | Status |
| --- | --- |
| OpenAI non-streaming chat completions | ✅ |
| OpenAI streaming (SSE) | planned |
| Anthropic Messages | planned |
| Google Gemini | planned |
| MCP stdio proxy | planned |
| MCP HTTP proxy | planned |
| CLI (`wickd start`) | planned |
| Budget enforcement | planned |

## Programmatic use

```ts
import { Store } from "wickd-core";
import { createProxy, DEFAULT_CONFIG } from "wickd-proxy";
import { serve } from "@hono/node-server";

const store = new Store("./wickd.db");
const proxy = createProxy({
  store,
  config: { ...DEFAULT_CONFIG, dbPath: "./wickd.db" },
});

serve({ fetch: proxy.app.fetch, port: DEFAULT_CONFIG.port });
```

## Run attribution

Each request attaches to a run in one of two ways:

1. **Explicit.** Set `x-wickd-run-id: <run-id>` on the request. If that run exists, the span attaches to it.
2. **Session (default).** Consecutive requests within 30 seconds share one run. After 30s of idle time, the previous run is closed and a new one starts.

## Boundaries

- Forwards auth headers unchanged. API keys never hit Wickd's own storage.
- Strips hop-by-hop headers (RFC 7230 §6.1).
- Fails closed on upstream errors: span records `status = 'error'` and the upstream status propagates to the client.
- Network failures return 502 to the caller; no silent success.
