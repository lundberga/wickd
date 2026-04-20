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

describe("Google Gemini streaming proxy", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("selects streaming based on the URL method and keeps the last usageMetadata", async () => {
    const chunks = [
      'data: {"candidates":[{"content":{"parts":[{"text":"He"}]}}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":1,"totalTokenCount":6}}\n\n',
      'data: {"candidates":[{"content":{"parts":[{"text":"llo"}]}}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}\n\n',
      'data: {"candidates":[{"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":4,"totalTokenCount":9}}\n\n',
    ];
    let seenUrl: string | undefined;
    const fetchFn = vi.fn(async (input) => {
      seenUrl = typeof input === "string" ? input : input.toString();
      return sseResponse(chunks);
    }) as typeof globalThis.fetch;

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: fetchFn,
    });

    const response = await proxy.app.request(
      "/google/v1beta/models/gemini-1.5-pro:streamGenerateContent?alt=sse",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ contents: [] }),
      },
    );

    expect(response.status).toBe(200);
    expect(seenUrl).toBe(
      "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:streamGenerateContent?alt=sse",
    );
    const body = await readBody(response);
    expect(body).toBe(chunks.join(""));

    const run = proxy.runTracker.currentRun;
    await waitForSpan(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBe(5);
    expect(spans[0]?.tokensOutput).toBe(4);
  });

  it("records null tokens when streaming response omits usageMetadata", async () => {
    const chunks = [
      'data: {"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}\n\n',
    ];

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: vi.fn(async () => sseResponse(chunks)) as typeof globalThis.fetch,
    });

    const response = await proxy.app.request(
      "/google/v1beta/models/gemini-1.5-flash:streamGenerateContent",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ contents: [] }),
      },
    );
    await readBody(response);

    const run = proxy.runTracker.currentRun;
    await waitForSpan(store, run!.id);
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.tokensInput).toBeNull();
    expect(spans[0]?.tokensOutput).toBeNull();
  });
});
