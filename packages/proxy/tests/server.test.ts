import { Store } from "wickd-core";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_CONFIG, createProxy } from "../src/index.js";

describe("proxy server", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("responds ok on /health", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
    });
    const res = await proxy.app.request("/health");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("returns 404 on an unknown route", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
    });
    const res = await proxy.app.request("/nonexistent");
    expect(res.status).toBe(404);
  });

  it("closes the session run on shutdown", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: async () =>
        new Response(JSON.stringify({ usage: { prompt_tokens: 1, completion_tokens: 1 } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    });

    await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", messages: [] }),
    });

    const run = proxy.runTracker.currentRun;
    expect(run?.status).toBe("running");

    proxy.shutdown();

    expect(store.getRun(run!.id)?.status).toBe("completed");
    expect(proxy.runTracker.currentRun).toBeNull();
  });
});
