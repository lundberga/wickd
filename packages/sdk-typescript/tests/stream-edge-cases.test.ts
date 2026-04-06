import { describe, it, expect } from "vitest";

// wrapAsyncStream and finalizeStream are internal, so we test the exact
// logic pattern from the interceptor inline — same approach as streaming.test.ts.

interface StreamState {
  recorded: boolean;
  inputTokens: number;
  outputTokens: number;
  finalizeCallCount: number;
}

function makeState(): StreamState {
  return { recorded: false, inputTokens: 0, outputTokens: 0, finalizeCallCount: 0 };
}

function finalizeStream(state: StreamState): void {
  if (state.recorded) return;
  state.recorded = true;
  state.finalizeCallCount++;
}

function wrapAsyncStream(
  stream: AsyncIterable<unknown>,
  state: StreamState,
): AsyncIterableIterator<unknown> {
  const iter = stream[Symbol.asyncIterator]();
  const wrapped: AsyncIterableIterator<unknown> = {
    [Symbol.asyncIterator]() { return wrapped; },
    async next() {
      let result: IteratorResult<unknown>;
      try {
        result = await iter.next();
      } catch (err) {
        finalizeStream(state);
        throw err;
      }
      if (result.done) finalizeStream(state);
      return result;
    },
    async return(value?: unknown) {
      finalizeStream(state);
      return iter.return?.(value) ?? { done: true as const, value: undefined };
    },
  };
  return wrapped;
}

async function* bombAfter(chunks: unknown[], after: number): AsyncGenerator<unknown> {
  for (let i = 0; ; i++) {
    if (i === after) throw new Error("mid-stream failure");
    if (i >= chunks.length) return;
    yield chunks[i];
  }
}

describe("stream edge cases", () => {
  describe("error mid-iteration finalizes", () => {
    it("calls finalize when stream throws after yielding chunks", async () => {
      const state = makeState();
      const chunks = ["a", "b", "c"];
      const source = bombAfter(chunks, 2); // throws after yielding "a", "b"
      const wrapped = wrapAsyncStream(source, state);

      const collected: unknown[] = [];
      await expect(async () => {
        for await (const chunk of wrapped) {
          collected.push(chunk);
        }
      }).rejects.toThrow("mid-stream failure");

      expect(collected).toEqual(["a", "b"]);
      expect(state.recorded).toBe(true);
    });

    it("calls finalize when stream throws immediately", async () => {
      const state = makeState();
      const source = bombAfter([], 0); // throws on first next()
      const wrapped = wrapAsyncStream(source, state);

      await expect(async () => {
        for await (const _ of wrapped) { /* nothing */ }
      }).rejects.toThrow("mid-stream failure");

      expect(state.recorded).toBe(true);
    });

    it("calls finalize on normal exhaustion", async () => {
      const state = makeState();

      async function* source() {
        yield "x";
        yield "y";
      }

      const wrapped = wrapAsyncStream(source(), state);
      const collected: unknown[] = [];
      for await (const chunk of wrapped) {
        collected.push(chunk);
      }

      expect(collected).toEqual(["x", "y"]);
      expect(state.recorded).toBe(true);
    });
  });

  describe("finalize is idempotent", () => {
    it("calling finalize multiple times does not increment count beyond 1", () => {
      const state = makeState();

      finalizeStream(state);
      finalizeStream(state);
      finalizeStream(state);

      expect(state.finalizeCallCount).toBe(1);
      expect(state.recorded).toBe(true);
    });

    it("error path followed by return() does not double-finalize", async () => {
      const state = makeState();
      const source = bombAfter(["a"], 1); // throws after "a"
      const wrapped = wrapAsyncStream(source, state);

      await expect(async () => {
        for await (const _ of wrapped) { /* nothing */ }
      }).rejects.toThrow("mid-stream failure");

      // Simulate a caller also calling return() after the error.
      await wrapped.return?.();

      expect(state.finalizeCallCount).toBe(1);
    });
  });

  describe("early return via return() finalizes", () => {
    it("calling return() on the iterator finalizes state", async () => {
      const state = makeState();

      async function* source() {
        yield "a";
        yield "b";
        yield "c";
      }

      const wrapped = wrapAsyncStream(source(), state);
      // Consume one chunk then break (which triggers return()).
      for await (const _ of wrapped) {
        break;
      }

      expect(state.recorded).toBe(true);
    });
  });
});
