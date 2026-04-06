# CLAUDE.md

## What is Wickd

Runtime SDK (Python + TypeScript) that adds guardrails to AI agents: budget limits, kill switches, human approval gates, and run traces. Intercepts LLM API calls at the SDK level and enforces constraints before the response reaches user code.

## Monorepo structure

```
packages/
  core/               Shared types, trace schema, cost-per-token tables.
  sdk-python/         Python SDK published to PyPI as `wickd`.
  sdk-typescript/     TypeScript SDK published to npm as `wickd`.
  proxy/              LLM proxy server for zero-code-change integration.
examples/             Framework-specific examples (LangGraph, CrewAI, OpenAI, Anthropic).
```

## Running tests

### Python SDK

```bash
cd packages/sdk-python
PYTHONPATH=. python3 -m pytest tests/ -v
```

### TypeScript SDK

```bash
cd packages/sdk-typescript
npx vitest run
```

### Build all TypeScript packages

```bash
npm install && npm run build
```

## Key architectural decisions

### Interceptor pattern

Wickd patches LLM SDK client methods (OpenAI, Anthropic, Google) when an agent run starts. Budget tracking works without requiring users to change their LLM calls. Patching happens inside the `@wickd.agent()` decorator / `agent()` wrapper, not at module level. A provider registry drives all three providers through a single generic patcher.

### Per-agent context tracking

- **Python**: `contextvars.ContextVar` for concurrent async agent isolation.
- **TypeScript**: `AsyncLocalStorage` for the same purpose.

### Reliability layers

1. SDK-level patching (primary) with sentinel-based verification
2. Transport-layer fallback (httpx interception when SDK patching fails)
3. LLM proxy mode (zero patching — just change `base_url`)

### Budget enforcement

Budget checks happen *inside* the patched SDK calls. When a response comes back, Wickd updates cost and checks against the cap *before* returning to user code. Raises `BudgetExceeded` immediately.

### Streaming support

Streaming responses are wrapped in `WickdSyncStream` / `WickdAsyncStream` that yield chunks untouched while accumulating token counts. Cost is recorded on stream exhaustion.

## Style guidelines

- No unnecessary abstractions. Minimal API surface.
- Minimal dependencies. Near-zero weight.
- Framework-agnostic. Never import LangGraph, CrewAI, or any agent framework.
- Developer-first. "Add Wickd in 60 seconds" DX.
