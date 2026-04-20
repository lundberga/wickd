import { Store } from "wickd-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_CONFIG, createProxy } from "../src/index.js";

/**
 * Build a Response whose body is an SSE stream of the given chunks. The
 * encoder deliberately emits each chunk as a separate ReadableStream pull
 * so the parser side exercises its buffering logic.
 */
function sseResponse(
  chunks: readonly string[],
  status = 200,
): Response {
  const encoder = new TextEncoder();
  let i = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[i] ?? ""));
      i++;
    },
  });
  return new Response(stream, {
    status,
    headers: { "content-type": "text/event-stream" },
  });
}

async function readFullBody(response: Response): Promise<string> {
  if (response.body === null) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let out = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    out += decoder.decode(value, { stream: true });
  }
  out += decoder.decode();
  return out;
}

// Wait for the background parser to finish and end the span.
async function waitForSpanToEnd(
  store: Store,
  runId: string,
  timeoutMs = 500,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const spans = store.getSpansForRun(runId);
    if (spans.every((s) => s.status !== "pending")) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("timed out waiting for span to end");
}

describe("OpenAI streaming proxy", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("pipes SSE chunks to the client verbatim and records usage", async () => {
    const chunks = [
      'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
      'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
      'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n',
      "data: [DONE]\n\n",
    ];
    let seenBody: unknown;
    const fetchFn = vi.fn(async (_url, init) => {
      seenBody = init?.body;
      return sseResponse(chunks);
    }) as typeof globalThis.fetch;

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: fetchFn,
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "gpt-4o",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream");

    const body = await readFullBody(response);
    expect(body).toBe(chunks.join(""));

    const run = proxy.runTracker.currentRun;
    expect(run).not.toBeNull();
    await waitForSpanToEnd(store, run!.id);

    const spans = store.getSpansForRun(run!.id);
    expect(spans).toHaveLength(1);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBe(7);
    expect(spans[0]?.tokensOutput).toBe(2);
    expect(spans[0]?.costUsd).toBeGreaterThan(0);

    // include_usage should have been injected into the upstream body.
    const forwardedBody = JSON.parse(seenBody as string) as {
      stream_options?: { include_usage?: boolean };
    };
    expect(forwardedBody.stream_options?.include_usage).toBe(true);
  });

  it("preserves existing stream_options when injecting include_usage", async () => {
    let seenBody: unknown;
    const fetchFn = vi.fn(async (_url, init) => {
      seenBody = init?.body;
      return sseResponse(["data: [DONE]\n\n"]);
    }) as typeof globalThis.fetch;

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: fetchFn,
    });

    await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "gpt-4o",
        stream: true,
        stream_options: { some_other_flag: "keep-me" },
      }),
    });

    const parsed = JSON.parse(seenBody as string) as {
      stream_options: Record<string, unknown>;
    };
    expect(parsed.stream_options).toEqual({
      some_other_flag: "keep-me",
      include_usage: true,
    });
  });

  it("records null tokens when upstream never emits a usage chunk", async () => {
    const chunks = [
      'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
      "data: [DONE]\n\n",
    ];
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(async () => sseResponse(chunks)) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", stream: true, messages: [] }),
    });
    await readFullBody(response);

    const run = proxy.runTracker.currentRun;
    await waitForSpanToEnd(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBeNull();
    expect(spans[0]?.tokensOutput).toBeNull();
  });

  it("ignores malformed data lines without breaking the stream", async () => {
    const chunks = [
      "data: not-json\n\n",
      'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
      'data: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
      "data: [DONE]\n\n",
    ];
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(async () => sseResponse(chunks)) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", stream: true }),
    });
    await readFullBody(response);

    const run = proxy.runTracker.currentRun;
    await waitForSpanToEnd(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBe(1);
  });

  it("propagates upstream errors on streaming requests", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "nope" }), {
            status: 401,
            headers: { "content-type": "application/json" },
          }),
      ) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", stream: true }),
    });
    expect(response.status).toBe(401);

    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("error");
    expect(spans[0]?.error).toContain("401");
  });

  it("records error span when upstream stream errors mid-flight", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"choices":[{"delta":{"content":"x"}}]}\n\n'));
        controller.error(new Error("ECONNRESET"));
      },
    });

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(
        async () =>
          new Response(stream, {
            status: 200,
            headers: { "content-type": "text/event-stream" },
          }),
      ) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", stream: true }),
    });
    // Client-side read will surface the same error, but the proxy should
    // still end the span with status=error rather than hanging it forever.
    try {
      await readFullBody(response);
    } catch {
      // Expected.
    }

    const run = proxy.runTracker.currentRun;
    await waitForSpanToEnd(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("error");
    expect(spans[0]?.error).toContain("ECONNRESET");
  });

  it("returns 502 when upstream is unreachable for streaming requests", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/openai/v1/chat/completions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "gpt-4o", stream: true }),
    });
    expect(response.status).toBe(502);
  });
});
