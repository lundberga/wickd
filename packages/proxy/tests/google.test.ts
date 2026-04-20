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

const GEMINI_RESPONSE = {
  candidates: [
    {
      content: {
        role: "model",
        parts: [{ text: "hi" }],
      },
      finishReason: "STOP",
    },
  ],
  usageMetadata: {
    promptTokenCount: 9,
    candidatesTokenCount: 2,
    totalTokenCount: 11,
  },
};

describe("Google Gemini proxy (non-streaming)", () => {
  let store: Store;

  beforeEach(() => {
    store = new Store(":memory:");
  });
  afterEach(() => {
    store.close();
  });

  it("forwards generateContent, preserves the query string, and records usage", async () => {
    let seenUrl: string | undefined;
    const fetchFn = mockFetch((url) => {
      seenUrl = url;
      return { status: 200, body: GEMINI_RESPONSE };
    });

    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: fetchFn,
    });

    const response = await proxy.app.request(
      "/google/v1beta/models/gemini-1.5-pro:generateContent?key=abc",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          contents: [{ role: "user", parts: [{ text: "hi" }] }],
        }),
      },
    );

    expect(response.status).toBe(200);
    expect(seenUrl).toBe(
      "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key=abc",
    );

    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.provider).toBe("google");
    expect(spans[0]?.model).toBe("gemini-1.5-pro");
    expect(spans[0]?.tokensInput).toBe(9);
    expect(spans[0]?.tokensOutput).toBe(2);
  });

  it("falls back to model='unknown' for malformed paths", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({ status: 200, body: GEMINI_RESPONSE })),
    });

    await proxy.app.request(
      "/google/v1beta/models/weird-shape",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ contents: [] }),
      },
    );

    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.model).toBe("unknown");
  });

  it("records null tokens when upstream omits usageMetadata", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({
        status: 200,
        body: { candidates: [] },
      })),
    });

    await proxy.app.request(
      "/google/v1beta/models/gemini-1.5-flash:generateContent",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ contents: [] }),
      },
    );

    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("ok");
    expect(spans[0]?.tokensInput).toBeNull();
    expect(spans[0]?.tokensOutput).toBeNull();
  });

  it("propagates upstream error status", async () => {
    const proxy = createProxy({
      store,
      config: { ...DEFAULT_CONFIG, dbPath: ":memory:" },
      fetch: mockFetch(() => ({
        status: 403,
        body: { error: { code: 403, status: "PERMISSION_DENIED" } },
      })),
    });

    const response = await proxy.app.request(
      "/google/v1beta/models/gemini-1.5-pro:generateContent",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ contents: [] }),
      },
    );

    expect(response.status).toBe(403);
    const run = proxy.runTracker.currentRun;
    const spans = store.getSpansForRun(run!.id);
    expect(spans[0]?.status).toBe("error");
  });
});
