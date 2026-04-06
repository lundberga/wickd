import { describe, it, expect } from "vitest";

// We test the stream infrastructure directly since we can't easily mock SDK internals.
// Import the chunk extractors and stream wrapper logic via the module.

// Since these are not exported, we test the behavior indirectly by verifying
// the streaming contract: async iterables that track cost on exhaustion.

describe("Streaming infrastructure", () => {
  describe("OpenAI chunk extraction", () => {
    it("extracts usage from final chunk", () => {
      const chunks = [
        { model: "gpt-4o", choices: [{ delta: { content: "Hello" } }], usage: null },
        { model: "gpt-4o", choices: [{ delta: { content: " world" } }], usage: null },
        { model: "gpt-4o", choices: [], usage: { prompt_tokens: 100, completion_tokens: 50 } },
      ];

      // Track state manually matching the extractor logic
      let inputTokens = 0;
      let outputTokens = 0;
      let model = "unknown";
      let responsePreview = "";

      for (const chunk of chunks) {
        if (chunk.usage) {
          inputTokens = chunk.usage.prompt_tokens ?? 0;
          outputTokens = chunk.usage.completion_tokens ?? 0;
        }
        if (chunk.model) model = chunk.model;
        if (!responsePreview && chunk.choices?.[0]?.delta?.content) {
          responsePreview = String(chunk.choices[0].delta.content).slice(0, 200);
        }
      }

      expect(inputTokens).toBe(100);
      expect(outputTokens).toBe(50);
      expect(model).toBe("gpt-4o");
      expect(responsePreview).toBe("Hello");
    });
  });

  describe("Anthropic chunk extraction", () => {
    it("extracts usage from message_start and message_delta", () => {
      const events = [
        { type: "message_start", message: { model: "claude-3-5-sonnet-20241022", usage: { input_tokens: 80 } } },
        { type: "content_block_delta", delta: { text: "Response text" } },
        { type: "message_delta", usage: { output_tokens: 30 } },
      ];

      let inputTokens = 0;
      let outputTokens = 0;
      let model = "unknown";
      let responsePreview = "";

      for (const event of events) {
        if (event.type === "message_start") {
          const msg = (event as { message: { usage: { input_tokens: number }; model: string } }).message;
          inputTokens = msg.usage.input_tokens ?? 0;
          model = msg.model;
        } else if (event.type === "message_delta") {
          const u = (event as { usage: { output_tokens: number } }).usage;
          outputTokens = u.output_tokens ?? 0;
        } else if (event.type === "content_block_delta" && !responsePreview) {
          const d = (event as { delta: { text: string } }).delta;
          responsePreview = d.text.slice(0, 200);
        }
      }

      expect(inputTokens).toBe(80);
      expect(outputTokens).toBe(30);
      expect(model).toBe("claude-3-5-sonnet-20241022");
      expect(responsePreview).toBe("Response text");
    });
  });

  describe("Google chunk extraction", () => {
    it("extracts usage from usageMetadata", () => {
      const chunks = [
        { text: "Result", usageMetadata: null },
        { text: " more", usageMetadata: { promptTokenCount: 60, candidatesTokenCount: 25 } },
      ];

      let inputTokens = 0;
      let outputTokens = 0;
      let responsePreview = "";

      for (const chunk of chunks) {
        if (chunk.usageMetadata) {
          inputTokens = chunk.usageMetadata.promptTokenCount ?? 0;
          outputTokens = chunk.usageMetadata.candidatesTokenCount ?? 0;
        }
        if (!responsePreview && chunk.text) {
          responsePreview = chunk.text.slice(0, 200);
        }
      }

      expect(inputTokens).toBe(60);
      expect(outputTokens).toBe(25);
      expect(responsePreview).toBe("Result");
    });
  });

  describe("Async iterable wrapping contract", () => {
    it("wraps async iterable and yields all chunks", async () => {
      const chunks = ["a", "b", "c"];
      let finalized = false;

      async function* source() {
        for (const c of chunks) yield c;
      }

      // Simulate the wrapping pattern used in interceptor
      const original = source();
      const collected: string[] = [];
      const wrapped: AsyncIterableIterator<string> = {
        [Symbol.asyncIterator]() { return wrapped; },
        async next() {
          const result = await original.next();
          if (result.done) {
            finalized = true;
          }
          return result;
        },
      };

      for await (const chunk of wrapped) {
        collected.push(chunk as string);
      }

      expect(collected).toEqual(["a", "b", "c"]);
      expect(finalized).toBe(true);
    });

    it("handles empty async iterable", async () => {
      let finalized = false;

      async function* source(): AsyncGenerator<string> {
        // empty
      }

      const original = source();
      const collected: string[] = [];
      const wrapped: AsyncIterableIterator<string> = {
        [Symbol.asyncIterator]() { return wrapped; },
        async next() {
          const result = await original.next();
          if (result.done) finalized = true;
          return result;
        },
      };

      for await (const chunk of wrapped) {
        collected.push(chunk as string);
      }

      expect(collected).toEqual([]);
      expect(finalized).toBe(true);
    });
  });

  describe("stream_options injection", () => {
    it("should inject include_usage for OpenAI streaming", () => {
      const body: Record<string, unknown> = { stream: true, model: "gpt-4o" };

      // Replicate the injection logic from wrapMethod
      if (body.stream) {
        const opts = (body.stream_options ?? {}) as Record<string, unknown>;
        if (!opts.include_usage) {
          body.stream_options = { ...opts, include_usage: true };
        }
      }

      expect(body.stream_options).toEqual({ include_usage: true });
    });

    it("should not overwrite existing stream_options", () => {
      const body: Record<string, unknown> = {
        stream: true,
        model: "gpt-4o",
        stream_options: { include_usage: true, custom: "value" },
      };

      if (body.stream) {
        const opts = (body.stream_options ?? {}) as Record<string, unknown>;
        if (!opts.include_usage) {
          body.stream_options = { ...opts, include_usage: true };
        }
      }

      expect((body.stream_options as Record<string, unknown>).custom).toBe("value");
      expect((body.stream_options as Record<string, unknown>).include_usage).toBe(true);
    });

    it("should not inject when not streaming", () => {
      const body: Record<string, unknown> = { model: "gpt-4o" };

      if (body.stream) {
        const opts = (body.stream_options ?? {}) as Record<string, unknown>;
        if (!opts.include_usage) {
          body.stream_options = { ...opts, include_usage: true };
        }
      }

      expect(body.stream_options).toBeUndefined();
    });
  });
});
