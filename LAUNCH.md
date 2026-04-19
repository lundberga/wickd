# Launch copy — Wickd v0.5.0

## Hacker News (Show HN)

**Title:** Show HN: Wickd — runtime safety net for AI agents (budget caps, kill switches, approvals)

**Body:**

Hi HN — I built Wickd after my first $500 surprise bill from a runaway Claude agent. It's a small SDK (Python + TypeScript) you wrap around an agent function, and it intercepts LLM SDK calls at the method level to enforce:

- Per-run / daily / monthly dollar caps across OpenAI, Anthropic, and Google
- Non-cost kill switches: `max_llm_calls`, `max_tool_calls`, `max_duration_seconds` — the runaway-loop guard I needed
- Human-approval gates before sensitive MCP tool calls
- Streaming-safe cost tracking (injects `stream_options.include_usage` for OpenAI, consumes Anthropic's stream usage events)
- Per-agent context isolation via `contextvars` / `AsyncLocalStorage` for concurrent runs

Zero proxy, zero sidecar, zero network hop — it's in-process. One decorator.

```python
@wickd.agent(budget=wickd.Budget(per_run=0.50, max_tool_calls=50))
def my_agent(task):
    ...
```

There's also an optional reverse-proxy mode (`wickd-proxy start`) for cases where monkey-patching isn't acceptable — same guarantees, just set `OPENAI_BASE_URL`.

Everything is MIT, 351 tests including live calls against all three providers. Not trying to be a gateway (Portkey/Runlayer do that at the network layer); this is specifically for protecting the agent from itself.

- Repo: https://github.com/lundberga/wickd
- Site: https://wickd.dev
- Python: `pip install wickd-ai`
- TypeScript: `npm install wickd`

Would love feedback on the runaway-guard API and what other providers/tools people want covered.

---

## Twitter / X thread

**1/ 🧵**

I lost $500 to a runaway LangChain agent last quarter. So I built Wickd — a runtime safety net you drop into any AI agent in one line.

Budget caps. Kill switches. Approval gates. Zero proxy.

`pip install wickd-ai`
`npm install wickd`

**2/**

The missing primitive: agent-level cost attribution *across providers*.

Your agent burns $2 on OpenAI + $1.50 on Anthropic + $0.80 on Gemini in a single run. No gateway sees that total — it's scattered across three network boundaries.

Wickd sees it because it lives inside the agent.

**3/**

Runaway-loop guards, separate from dollar caps:

```python
Budget(
  per_run=1.00,
  max_llm_calls=20,
  max_tool_calls=50,
  max_duration_seconds=60,
)
```

Any cap trips → `BudgetExceeded` with the specific trigger. Trace records which guard fired.

**4/**

MCP-aware out of the box. Every `ClientSession.call_tool()` is tracked automatically. Flag dangerous tools for human approval:

```python
mcp_approval_required=["drop_table", "send_email"]
```

Pauses on those calls, resumes on approval. Works with Slack/webhook/CLI handlers.

**5/**

If patching isn't acceptable: run the proxy, set `OPENAI_BASE_URL`, zero code changes. Same budget enforcement, now at the network layer.

**6/**

MIT. 351 tests. Python + TypeScript parity. Works with every agent framework because it patches at the SDK level, not the framework level.

Repo + docs: https://wickd.dev

---

## LinkedIn post

I lost $500 to a runaway AI agent. That's how Wickd started.

Today I'm open-sourcing it.

Wickd is a runtime safety net for AI agents. You wrap your agent function with one decorator, and every LLM call — OpenAI, Anthropic, Google — is intercepted in-process. Budget caps, kill switches, approval gates. Zero proxy, zero network hop.

Why this and not an LLM gateway?

Gateways (Portkey, Runlayer, Kong) protect your infrastructure from the network layer. Wickd protects the agent itself. Different problem: nobody else has cross-provider total spend in a single trace, because nobody else lives inside the agent.

If you've ever wondered "why did my agent burn $50 running overnight?" — Wickd is the answer I wanted when it happened to me.

MIT licensed. Python + TypeScript. Works with LangGraph, CrewAI, OpenAI Agents, Vercel AI SDK, or plain SDK code.

→ https://wickd.dev
→ https://github.com/lundberga/wickd

---

## Reddit /r/LocalLLaMA or /r/MachineLearning

**Title:** [Project] Wickd — drop-in budget + runaway guards for agent code (Python/TS, MIT)

Built this after a runaway loop cost me $500. It's an SDK that patches the OpenAI/Anthropic/Google SDK methods at runtime and enforces:

- Dollar caps (per_run / daily / monthly) across all 3 providers in one view
- Call-count caps (max_llm_calls, max_tool_calls) — the thing that actually stops infinite loops
- Wall-clock kill (max_duration_seconds)
- Human-approval gates for MCP tool calls flagged as dangerous

In-process, no proxy, one decorator. MIT, 351 tests incl. live provider calls.

`pip install wickd-ai` / `npm install wickd`

Feedback welcome — especially on the runaway-guard API.
