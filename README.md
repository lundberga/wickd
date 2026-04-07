<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/logo-dark.png">
  <img alt="Wickd" src=".github/assets/logo-dark.png" width="320">
</picture>

**The runtime safety net for AI agents.**

Budget limits, kill switches, and approval gates — inside your agent, across every provider.

[![npm](https://img.shields.io/npm/v/wickd?color=blue&label=npm)](https://www.npmjs.com/package/wickd)
[![PyPI](https://img.shields.io/pypi/v/wickd-ai?color=blue&label=pypi)](https://pypi.org/project/wickd-ai/)
[![CI](https://img.shields.io/github/actions/workflow/status/lundberga/wickd/ci.yml?branch=main&label=CI)](https://github.com/lundberga/wickd/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9+-blue)](https://pypi.org/project/wickd-ai/)
[![Node](https://img.shields.io/badge/node-18+-blue)](https://www.npmjs.com/package/wickd)

</div>

---

## Why Wickd?

| | |
|---|---|
| **Zero latency** | Runs in-process. No proxy, no sidecar, no network hop. |
| **43 models tracked** | Real-time pricing for OpenAI, Anthropic, and Google models. |
| **1 decorator** | Add budget enforcement to any agent in one line of code. |
| **Streaming-safe** | Tracks cost from streaming responses, including Anthropic `messages.stream()`. |
| **Concurrent-safe** | Per-run budget isolation via `ContextVar` / `AsyncLocalStorage`. |
| **292 tests** | Unit, integration, and real API tests across all 3 providers. |

---

## Quick Start

### Python

```bash
pip install wickd-ai
```

```python
import wickd
import openai

@wickd.agent(budget=wickd.Budget(per_run=0.50, daily=5.00))
def my_agent(task: str):
    client = openai.OpenAI()
    return client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": task}],
    )

try:
    result = my_agent.run("summarize this document")
except wickd.BudgetExceeded as e:
    print(f"Agent stopped: {e}")
```

### TypeScript

```bash
npm install wickd
```

```typescript
import { agent, Budget, BudgetExceeded } from "wickd";
import OpenAI from "openai";

const myAgent = agent({
  name: "my_agent",
  budget: new Budget({ perRun: 0.50, daily: 5.00 }),
  fn: async (task: string) => {
    const client = new OpenAI();
    return await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: task }],
    });
  },
});

await myAgent.run("summarize this document");
```

### Proxy Mode (zero code changes)

```bash
pip install wickd-proxy
wickd-proxy start --budget-per-run 0.50 --budget-daily 5.00

# Just set an env var — your existing code works unchanged
export OPENAI_BASE_URL=http://localhost:4319/openai/v1
```

---

## Supported Providers

| Provider | Models | Streaming | Async |
|----------|--------|:---------:|:-----:|
| **OpenAI** | GPT-4o, GPT-4.1, o1, o3, o4-mini, ... | ✅ | ✅ |
| **Anthropic** | Claude Opus 4, Sonnet 4, Haiku 4.5 | ✅ | ✅ |
| **Google** | Gemini 2.5 Pro, 2.5 Flash | ✅ | ✅ |

43 models with real-time pricing. Unknown models use conservative fallback estimates.

---

## Features

| Feature | Description |
|---------|-------------|
| **Budget enforcement** | Per-run, daily, and monthly cost caps. Checked before the response reaches your code. |
| **Kill switches** | Automatic halt when spend exceeds limits. Raises `BudgetExceeded` immediately. |
| **Approval gates** | Pause execution for human review. Slack, webhook, terminal, or custom handlers. |
| **Streaming support** | Tracks cost from streaming responses. Auto-injects `stream_options` for OpenAI. |
| **Tool tracking** | Trace MCP tool calls alongside LLM requests. Approval gates on dangerous tools. |
| **Patch verification** | Runtime health checks confirm interception is active. Configurable: block, warn, or allow. |
| **Transport fallback** | Falls back to httpx-level interception when SDK patching fails. |
| **Async agents** | Full `async/await` support with `arun()`. Context isolation across concurrent runs. |

---

## How It Works

Wickd intercepts LLM SDK calls at the method level. No separate server. No network hop.

```
Your agent code
      │
      ▼
  Wickd interceptor (budget check → trace → cost)
      │
      ▼
  OpenAI / Anthropic / Google SDK
      │
      ▼
  LLM API
```

When your agent calls `client.chat.completions.create()`, Wickd's wrapper:

1. Checks budget before the call
2. Forwards to the real SDK
3. Reads token usage from the response
4. Calculates cost and records it
5. Checks budget again — kills the agent if exceeded
6. Returns the response to your code

---

## Works With

| Framework | Status |
|-----------|:------:|
| OpenAI SDK | ✅ |
| Anthropic SDK | ✅ |
| Google GenAI | ✅ |
| LangGraph | ✅ |
| CrewAI | ✅ |
| Vercel AI SDK | ✅ |
| OpenAI Agents SDK | ✅ |
| Any Python/TS code | ✅ |

Wickd is framework-agnostic. It patches at the LLM SDK level, so it works with any agent framework or custom code.

---

## Packages

| Package | Install | Description |
|---------|---------|-------------|
| [`wickd`](https://www.npmjs.com/package/wickd) | `npm install wickd` | TypeScript SDK |
| [`wickd-ai`](https://pypi.org/project/wickd-ai/) | `pip install wickd-ai` | Python SDK |
| [`wickd-core`](https://www.npmjs.com/package/wickd-core) | `npm install wickd-core` | Shared types & pricing |
| [`wickd-proxy`](https://pypi.org/project/wickd-proxy/) | `pip install wickd-proxy` | LLM proxy server |

---

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

```bash
git clone https://github.com/lundberga/wickd.git
cd wickd
npm install && npm run build          # TypeScript
cd packages/sdk-python && pip install -e ".[dev]"  # Python
```

Run tests:

```bash
npm run build && cd packages/sdk-typescript && npx vitest run   # TypeScript
cd packages/sdk-python && PYTHONPATH=. python -m pytest tests/  # Python
cd packages/proxy && PYTHONPATH=../sdk-python:. python -m pytest tests/  # Proxy
```

---

## License

[MIT](LICENSE)
