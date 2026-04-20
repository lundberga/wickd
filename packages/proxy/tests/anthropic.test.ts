import { Store } from "wickd-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_CONFIG, createProxy } from "../src/index.js";

function mockFetch(
  responder: (url: string, init: RequestInit | undefined) => { status: number; body: unknown },
): typeof globalThis.fetch {
  return vi.fn(async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    const { status, body } = responder(url, init);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
  }) as typeof globalThis.fetch;
}

const ANTHROPIC_RESPONSE = {
  id: "msg_abc",
  type: "message",
  role: "assistant",
  model: "claude-sonnet-4-6",
  content: [{ type: "text", text: "hi" }],
  usage: { input_tokens: 14, output_tokens: 4 },
};

describe("Anthropic messages proxy (non-streaming)", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("forwards to /v1/messages and records a span with usage", async () => {
    let seenUrl: string | undefined;
    let seenAuth: string | null = null;
    const fetchFn = mockFetch((url, init) => {
      seenUrl = url;
      seenAuth = (init?.headers as Headers | undefined)?.get("x-api-key") ?? null;
      return { status: 200, body: ANTHROPIC_RESPONSE };
    });

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: fetchFn,
    });

    const response = await proxy.app.request("/anthropic/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": "sk-ant-test",
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-6",
        messages: [{ role: "user", content: "hi" }],
        max_tokens: 512,
      }),
    });

    expect(response.status).toBe(200);
    expect(seenUrl).toBe("https://api.anthropic.com/v1/messages");
    expect(seenAuth).toBe("sk-ant-test");

    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.provider).toBe("anthropic");
    expect(spans[0]?.model).toBe("claude-sonnet-4-6");
    expect(spans[0]?.tokensInput).toBe(14);
    expect(spans[0]?.tokensOutput).toBe(4);
    expect(spans[0]?.costUsd).toBeGreaterThan(0);
  });

  it("returns 400 on invalid JSON", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => {
        throw new Error("should not reach upstream");
      }),
    });

    const response = await proxy.app.request("/anthropic/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "not-json",
    });

    expect(response.status).toBe(400);
  });

  it("propagates upstream errors", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({
        status: 401,
        body: { type: "error", error: { type: "authentication_error" } },
      })),
    });

    const response = await proxy.app.request("/anthropic/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "claude-sonnet-4-6", messages: [] }),
    });

    expect(response.status).toBe(401);
    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("error");
  });
});
