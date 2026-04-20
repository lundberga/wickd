import { describe, expect, it } from "vitest";

import { SseParser } from "../src/streaming/sse.js";

describe("SseParser", () => {
  it("parses a single complete event", () => {
    const parser = new SseParser();
    const events = parser.push("data: hello\n\n");
    expect(events).toEqual([{ data: "hello" }]);
  });

  it("concatenates multi-line data fields", () => {
    const parser = new SseParser();
    const events = parser.push("data: line-1\ndata: line-2\n\n");
    expect(events).toEqual([{ data: "line-1\nline-2" }]);
  });

  it("captures event and id fields when present", () => {
    const parser = new SseParser();
    const events = parser.push("event: update\nid: 42\ndata: payload\n\n");
    expect(events).toEqual([{ data: "payload", event: "update", id: "42" }]);
  });

  it("buffers partial events across push calls", () => {
    const parser = new SseParser();
    expect(parser.push("data: hel")).toEqual([]);
    expect(parser.push("lo\n\n")).toEqual([{ data: "hello" }]);
  });

  it("emits multiple events from one chunk", () => {
    const parser = new SseParser();
    const events = parser.push("data: a\n\ndata: b\n\ndata: c\n\n");
    expect(events.map((e) => e.data)).toEqual(["a", "b", "c"]);
  });

  it("normalises CRLF to LF", () => {
    const parser = new SseParser();
    const events = parser.push("data: crlf\r\n\r\n");
    expect(events).toEqual([{ data: "crlf" }]);
  });

  it("normalises lone CR to LF", () => {
    const parser = new SseParser();
    const events = parser.push("data: cr\r\rdata: next\n\n");
    expect(events.map((e) => e.data)).toEqual(["cr", "next"]);
  });

  it("ignores comment lines", () => {
    const parser = new SseParser();
    const events = parser.push(": heartbeat\ndata: payload\n\n");
    expect(events).toEqual([{ data: "payload" }]);
  });

  it("ignores lines without a colon and empty lines within an event", () => {
    const parser = new SseParser();
    const events = parser.push("bogus\ndata: good\n\n");
    expect(events).toEqual([{ data: "good" }]);
  });

  it("treats 'data' with no value as empty string", () => {
    const parser = new SseParser();
    const events = parser.push("data:\n\n");
    expect(events).toEqual([{ data: "" }]);
  });

  it("omits events that have no data field", () => {
    const parser = new SseParser();
    const events = parser.push("event: ping\n\n");
    expect(events).toEqual([]);
  });

  it("handles OpenAI-style chunk stream", () => {
    const parser = new SseParser();
    const chunk = [
      'data: {"choices":[{"delta":{"content":"Hello"}}]}',
      "",
      'data: {"choices":[{"delta":{"content":" world"}}]}',
      "",
      'data: {"usage":{"prompt_tokens":5,"completion_tokens":2}}',
      "",
      "data: [DONE]",
      "",
      "",
    ].join("\n");

    const events = parser.push(chunk);
    expect(events).toHaveLength(4);
    expect(events[3]?.data).toBe("[DONE]");
  });

  it("flush() surfaces a trailing event not terminated by blank line", () => {
    const parser = new SseParser();
    expect(parser.push("data: tail")).toEqual([]);
    expect(parser.flush()).toEqual([{ data: "tail" }]);
  });

  it("flush() returns empty when buffer is empty", () => {
    const parser = new SseParser();
    expect(parser.flush()).toEqual([]);
  });
});
