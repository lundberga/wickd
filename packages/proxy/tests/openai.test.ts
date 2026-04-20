import { Store } from "wickd-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_CONFIG, createProxy } from "../src/index.js";

interface MockResponse {
  readonly status: number;
  readonly body: unknown;
  readonly headers?: Record<string, string>;
}

function mockFetch(
  responder: (url: string, init: RequestInit | undefined) => MockResponse | Error,
): typeof globalThis.fetch {
  return vi.fn(async (input: Parameters<typeof fetch>[0], init) => {
    const url = typeof input === "string" ? input : input.toString();
    const result = responder(url, init);
    if (result instanceof Error) throw result;
    return new Response(JSON.stringify(result.body), {
      status: result.status,
      headers: {
        "content-type": "application/json",
        ...(result.headers ?? {}),
      },
    });
  }) as typeof globalThis.fetch;
}

const OPENAI_RESPONSE = {
  id: "chatcmpl-abc",
  object: "chat.completion",
  model: "gpt-4o",
  choices: [
    {
      index: 0,
      message: { role: "assistant", content: "hello" },
      finish_reason: "stop",
    },
  ],
  usage: {
    prompt_tokens: 12,
    completion_tokens: 3,
    total_tokens: 15,
  },
};

describe("OpenAI chat completions proxy", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("forwards non-streaming requests and records a span with usage", async () => {
    const captured: { url?: string; body?: unknown; auth?: string | null } = {};
    const fetchFn = mockFetch((url, init) => {
      captured.url = url;
      captured.body = init?.body;
      captured.auth = (init?.headers as Headers | undefined)?.get("authorization") ?? null;
      return { status: 200, body: OPENAI_RESPONSE };
    });

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: fetchFn,
    });

    const requestBody = {
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
    };

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: "Bearer sk-test",
      },
      body: JSON.stringify(requestBody),
    });

    expect(response.status).toBe(200);
    const parsed = await response.json();
    expect(parsed).toEqual(OPENAI_RESPONSE);

    // Upstream should have received the original body and auth header.
    expect(captured.url).toBe("https://api.openai.com/v1/chat/completions");
    expect(captured.auth).toBe("Bearer sk-test");
    expect(JSON.parse(captured.body as string)).toEqual(requestBody);

    // The run + span should be persisted with extracted token counts.
    const run = proxy.runTracker.currentRun;
    expect(run).not.toBeNull();
    const spans = store.getSpansForRun(run!.id);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.model).toBe("gpt-4o");
    expect(spans[0]?.provider).toBe("openai");
    expect(spans[0]?.tokensInput).toBe(12);
    expect(spans[0]?.tokensOutput).toBe(3);
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

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "not-json",
    });

    expect(response.status).toBe(400);
  });

  it("propagates upstream error status codes", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({
        status: 429,
        body: { error: { message: "rate limited", type: "rate_limit" } },
      })),
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", messages: [] }),
    });

    expect(response.status).toBe(429);
    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("error");
    expect(spans[0]?.error).toContain("429");
  });

  it("returns 502 when the upstream is unreachable", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => new Error("ECONNREFUSED")),
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", messages: [] }),
    });

    expect(response.status).toBe(502);
    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("error");
    expect(spans[0]?.error).toContain("ECONNREFUSED");
  });

  it("strips hop-by-hop headers before forwarding", async () => {
    let forwardedHeaders: Headers | undefined;
    const fetchFn = mockFetch((_url, init) => {
      forwardedHeaders = init?.headers as Headers;
      return { status: 200, body: OPENAI_RESPONSE };
    });

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: fetchFn,
    });

    await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        connection: "keep-alive",
        "transfer-encoding": "chunked",
        "x-custom": "keep-me",
      },
      body: JSON.stringify({ model: "gpt-4o", messages: [] }),
    });

    expect(forwardedHeaders?.get("connection")).toBeNull();
    expect(forwardedHeaders?.get("transfer-encoding")).toBeNull();
    expect(forwardedHeaders?.get("x-custom")).toBe("keep-me");
    expect(forwardedHeaders?.get("content-type")).toBe("application/json");
  });

  it("records null token counts when upstream omits usage", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({
        status: 200,
        body: { id: "x", model: "gpt-4o", choices: [] },
      })),
    });

    await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", messages: [] }),
    });

    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBeNull();
    expect(spans[0]?.tokensOutput).toBeNull();
    expect(spans[0]?.costUsd).toBe(0);
  });

  it("handles upstream responses that are not JSON objects", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(
        async () =>
          new Response("not-json-at-all", {
            status: 200,
            headers: { "content-type": "text/plain" },
          }),
      ) as typeof globalThis.fetch,
    });

    const res = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", messages: [] }),
    });

    // Upstream succeeded from HTTP's perspective, so we proxy the 200 through.
    expect(res.status).toBe(200);
    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBeNull();
  });

  it("rejects request bodies that are not JSON objects", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => {
        throw new Error("should not reach upstream");
      }),
    });

    const res = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify([{ model: "gpt-4o" }]),
    });

    expect(res.status).toBe(400);
  });

  it("falls back to 'unknown' when body.model is missing or non-string", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({ status: 200, body: OPENAI_RESPONSE })),
    });

    await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: 42, messages: [] }),
    });

    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.model).toBe("unknown");
  });

  it("honors x-wickd-run-id and attaches span to that run", async () => {
    const explicit = store.startRun({ name: "explicit-agent" });
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({ status: 200, body: OPENAI_RESPONSE })),
    });

    await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-wickd-run-id": explicit.id,
      },
      body: JSON.stringify({ model: "gpt-4o", messages: [] }),
    });

    const spans = store.getSpansForRun(explicit.id);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.tokensInput).toBe(12);
  });
});
