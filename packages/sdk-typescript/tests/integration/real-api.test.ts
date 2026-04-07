/**
 * Integration tests that make real LLM API calls.
 * Costs ~$0.01 per full run. Requires API keys in .env or environment.
 * Skipped automatically if keys are not present.
 */

import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { agent, Budget, BudgetExceeded, patchAll, verifyPatches } from "../../src/index.js";

// ── Load env from repo root ──────────────────────────────────────────────────

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const envPath = path.resolve(__dirname, "../../../../.env");

if (fs.existsSync(envPath)) {
  const raw = fs.readFileSync(envPath, "utf-8");
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const eqIdx = trimmed.indexOf("=");
    const key = trimmed.slice(0, eqIdx).trim();
    const value = trimmed.slice(eqIdx + 1).trim();
    if (key && value && !process.env[key]) {
      process.env[key] = value;
    }
  }
}

// Use createRequire so the interceptor's require() and these imports share the same
// CJS module cache — required for prototype patching to take effect.
const req = createRequire(fileURLToPath(import.meta.url));

// ── OpenAI ───────────────────────────────────────────────────────────────────

describe.skipIf(!process.env.OPENAI_API_KEY)("OpenAI — real API", () => {
  it("basic call tracks cost", async () => {
    const OpenAI = (req("openai") as { OpenAI: unknown }).OpenAI as new (opts?: Record<string, unknown>) => {
      chat: {
        completions: {
          create(body: Record<string, unknown>): Promise<{
            choices: Array<{ message: { content: string | null } }>;
          }>;
        };
      };
    };

    let traceEvent: Record<string, unknown> = {};

    const ask = agent({
      fn: async (question: string) => {
        const client = new OpenAI();
        const resp = await client.chat.completions.create({
          model: "gpt-4o-mini",
          messages: [{ role: "user", content: question }],
          max_tokens: 10,
        });
        return resp.choices[0].message.content ?? "";
      },
      budget: new Budget({ perRun: 1.0 }),
      onRunComplete: (e) => { traceEvent = e as Record<string, unknown>; },
    });

    const result = await ask.run("Say hi");
    expect(result.length).toBeGreaterThan(0);

    const summary = traceEvent.summary as Record<string, unknown>;
    expect(summary.runSpend).toBeGreaterThan(0);
    expect(summary.callCount).toBe(1);
  });

  it("streaming tracks cost", async () => {
    const OpenAI = (req("openai") as { OpenAI: unknown }).OpenAI as new (opts?: Record<string, unknown>) => {
      chat: {
        completions: {
          create(body: Record<string, unknown>): Promise<AsyncIterable<{
            choices: Array<{ delta: { content?: string } }>;
          }>>;
        };
      };
    };

    let traceEvent: Record<string, unknown> = {};

    const ask = agent({
      fn: async (question: string) => {
        const client = new OpenAI();
        const chunks: string[] = [];
        const stream = await client.chat.completions.create({
          model: "gpt-4o-mini",
          messages: [{ role: "user", content: question }],
          max_tokens: 10,
          stream: true,
        });
        for await (const chunk of stream) {
          const text = chunk.choices[0]?.delta?.content;
          if (text) chunks.push(text);
        }
        return chunks.join("");
      },
      budget: new Budget({ perRun: 1.0 }),
      onRunComplete: (e) => { traceEvent = e as Record<string, unknown>; },
    });

    const result = await ask.run("Say hello");
    expect(result.length).toBeGreaterThan(0);

    const summary = traceEvent.summary as Record<string, unknown>;
    expect(summary.runSpend).toBeGreaterThan(0);
    expect(summary.callCount).toBe(1);
  });

  it("budget kill stops agent", async () => {
    const OpenAI = (req("openai") as { OpenAI: unknown }).OpenAI as new (opts?: Record<string, unknown>) => {
      chat: {
        completions: {
          create(body: Record<string, unknown>): Promise<{
            choices: Array<{ message: { content: string | null } }>;
          }>;
        };
      };
    };

    const cheapAgent = agent({
      fn: async (question: string) => {
        const client = new OpenAI();
        const resp = await client.chat.completions.create({
          model: "gpt-4o-mini",
          messages: [{ role: "user", content: question }],
          max_tokens: 5,
        });
        return resp.choices[0].message.content ?? "";
      },
      budget: new Budget({ perRun: 0.000001 }),
    });

    await expect(cheapAgent.run("Hi")).rejects.toThrow(BudgetExceeded);
  });
});

// ── Anthropic ─────────────────────────────────────────────────────────────────

describe.skipIf(!process.env.ANTHROPIC_API_KEY)("Anthropic — real API", () => {
  it("basic call tracks cost", async () => {
    const Anthropic = (req("@anthropic-ai/sdk") as { default: unknown }).default as new (opts?: Record<string, unknown>) => {
      messages: {
        create(body: Record<string, unknown>): Promise<{
          content: Array<{ type: string; text: string }>;
        }>;
      };
    };

    let traceEvent: Record<string, unknown> = {};

    const ask = agent({
      fn: async (question: string) => {
        const client = new Anthropic();
        const resp = await client.messages.create({
          model: "claude-haiku-4-5-20251001",
          max_tokens: 10,
          messages: [{ role: "user", content: question }],
        });
        const block = resp.content[0];
        return block.type === "text" ? block.text : "";
      },
      budget: new Budget({ perRun: 1.0 }),
      onRunComplete: (e) => { traceEvent = e as Record<string, unknown>; },
    });

    const result = await ask.run("Say hi");
    expect(result.length).toBeGreaterThan(0);

    const summary = traceEvent.summary as Record<string, unknown>;
    expect(summary.runSpend).toBeGreaterThan(0);
    expect(summary.callCount).toBe(1);
  });
});

// ── Google ────────────────────────────────────────────────────────────────────

describe.skipIf(!process.env.GOOGLE_API_KEY)("Google — real API", () => {
  it("basic call tracks cost", async () => {
    const { GoogleGenAI } = req("@google/genai") as {
      GoogleGenAI: new (opts: { apiKey: string | undefined }) => {
        models: {
          generateContent(params: { model: string; contents: string }): Promise<{ text: string }>;
        };
      };
    };

    let traceEvent: Record<string, unknown> = {};

    const ask = agent({
      fn: async (question: string) => {
        const client = new GoogleGenAI({ apiKey: process.env.GOOGLE_API_KEY });
        const resp = await client.models.generateContent({
          model: "gemini-2.5-flash",
          contents: question,
        });
        return resp.text ?? "";
      },
      budget: new Budget({ perRun: 1.0 }),
      onRunComplete: (e) => { traceEvent = e as Record<string, unknown>; },
    });

    const result = await ask.run("Say hi");
    expect(result.length).toBeGreaterThan(0);

    const summary = traceEvent.summary as Record<string, unknown>;
    expect(summary.runSpend).toBeGreaterThan(0);
    expect(summary.callCount).toBe(1);
  });
});

// ── Patch verification ────────────────────────────────────────────────────────

describe.skipIf(!process.env.OPENAI_API_KEY)("Patch verification", () => {
  it("verified after patchAll", () => {
    patchAll();
    const status = verifyPatches();
    expect(status.openai.installed).toBe(true);
    expect(status.openai.verified).toBe(true);
  });
});
