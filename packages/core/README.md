# wickd-core

Canonical data model and local storage for Wickd.

This package defines the shared vocabulary — `Run`, `Span`, `Pricing` — that every other Wickd package reads and writes. Everything persists to a local SQLite database managed by the `Store` class. No network, no auth, no assumptions about how spans are produced.

## What this package owns

- **Types.** `Run`, `Span`, `Pricing`, and the status unions used across the system.
- **Storage.** A thin `Store` class over SQLite (via `better-sqlite3`), with migrations, prepared statements, and transactional updates.
- **Cost catalog.** A default pricing table for OpenAI, Anthropic, and Google models, plus a `calculateCost()` helper and a pluggable catalog API for overrides.

## What this package does *not* own

- LLM proxying — that belongs to `wickd-proxy`.
- SDK integration (monkey-patching, framework hooks) — `wickd-sdk-*`.
- Visualization — `wickd-dashboard`.
- Multi-tenancy, auth, sync — `wickd-cloud`.

The boundary matters. Every brick above this one depends on `wickd-core` being stable and correct.

## Data model

```
Run 1 ─── * Span
         └── parent_span_id → Span (self-reference for nesting)
```

A `Run` is one execution of an agent. A `Span` is one call made during that run — to an LLM, an MCP tool, or an HTTP endpoint. Spans can nest. Cost is recorded on each span and rolled up to `Run.totalCostUsd`.

Timestamps are stored as unix-ms integers in SQLite and surfaced as `Date` in the API. Structured fields (`input`, `output`, `metadata`) are JSON-serialized.

## Usage

```ts
import { Store, calculateCost } from "wickd-core";

const store = new Store("./wickd.db");

const run = store.startRun({ name: "research-agent" });

const span = store.startSpan({
  runId: run.id,
  kind: "llm",
  name: "gpt-4o",
  model: "gpt-4o",
  provider: "openai",
});

store.endSpan(span.id, {
  status: "ok",
  tokensInput: 1_000,
  tokensOutput: 500,
});

store.endRun(run.id, { status: "completed" });

console.log(calculateCost("gpt-4o", 1_000, 500)); // 0.0075
```

## Guarantees

- **Transactional span completion.** Ending a span and bumping the parent run's cost happen in one SQLite transaction.
- **Idempotent migrations.** Re-running `migrate()` on an existing database is a no-op.
- **WAL mode.** Multiple readers can observe writes without blocking the writer.
- **Non-negative cost math.** `calculateCost()` rejects negative or non-finite token counts.
- **No silent fallbacks.** Ending an unknown or already-ended run/span throws a typed error.

## Scripts

```
npm run build      # emit ESM + d.ts via tsup
npm test           # run the vitest suite
npm run typecheck  # tsc --noEmit
```
