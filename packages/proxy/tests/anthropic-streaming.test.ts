import { Store } from "wickd-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_CONFIG, createProxy } from "../src/index.js";

function sseResponse(chunks: readonly string[], status = 200): Response {
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

async function readBody(response: Response): Promise<string> {
  if (response.body === null) return "";
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let out = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    out += decoder.decode(value, { stream: true });
  }
  return out + decoder.decode();
}

async function waitForSpan(
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
  throw new Error("span did not end in time");
}

describe("Anthropic streaming proxy", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("merges input_tokens from message_start with output_tokens from message_delta", async () => {
    const chunks = [
      "event: message_start\n",
      'data: {"type":"message_start","message":{"id":"m","usage":{"input_tokens":15,"output_tokens":1}}}\n\n',
      "event: content_block_delta\n",
      'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}\n\n',
      "event: message_delta\n",
      'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}\n\n',
      "event: message_stop\n",
      'data: {"type":"message_stop"}\n\n',
    ];

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(async () => sseResponse(chunks)) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/anthropic/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-sonnet-4-6",
        messages: [{ role: "user", content: "hi" }],
        stream: true,
      }),
    });

    expect(response.status).toBe(200);
    const body = await readBody(response);
    expect(body).toBe(chunks.join(""));

    const run = proxy.runTracker.currentRun;
    await waitForSpan(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBe(15);
    expect(spans[0]?.tokensOutput).toBe(42);
  });

  it("derives event type from the SSE event field when the data payload omits it", async () => {
    // Anthropic reliably sends type in the data body, but the parser must
    // still be able to handle servers that only set the SSE event field.
    const chunks = [
      "event: message_start\n",
      'data: {"message":{"usage":{"input_tokens":7,"output_tokens":1}}}\n\n',
      "event: message_delta\n",
      'data: {"usage":{"output_tokens":11}}\n\n',
    ];

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(async () => sseResponse(chunks)) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/anthropic/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        model: "claude-sonnet-4-6",
        stream: true,
      }),
    });
    await readBody(response);

    const run = proxy.runTracker.currentRun;
    await waitForSpan(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.tokensInput).toBe(7);
    expect(spans[0]?.tokensOutput).toBe(11);
  });

  it("ignores events without usage and malformed data lines", async () => {
    const chunks = [
      "event: ping\n",
      'data: {"type":"ping"}\n\n',
      "event: message_start\n",
      "data: not-json\n\n",
      "event: message_delta\n",
      'data: {"usage":{"output_tokens":3}}\n\n',
    ];

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(async () => sseResponse(chunks)) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request("/anthropic/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ model: "claude-sonnet-4-6", stream: true }),
    });
    await readBody(response);

    const run = proxy.runTracker.currentRun;
    await waitForSpan(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBeNull();
    expect(spans[0]?.tokensOutput).toBe(3);
  });
});
