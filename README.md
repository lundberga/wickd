# wickd

Runtime safety net for AI agents. Budget limits, kill switches, and approval gates — inside your agent, across every provider and tool.

```
pip install wickd
```

```
npm install wickd
```

## Quick start

### Python

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

```typescript
import { agent, Budget, BudgetExceeded } from "wickd";
import OpenAI from "openai";

const myAgent = agent({
  name: "my_agent",
  budget: new Budget({ perRun: 0.50, daily: 5.00 }),
  fn: async (task: string) => {
    const client = new OpenAI();
    const res = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: task }],
    });
    return res.choices[0].message.content;
  },
});

await myAgent.run("summarize this document");
```

### Proxy mode (zero code changes)

```bash
wickd-proxy start --budget-per-run 0.50 --budget-daily 5.00

# Point your SDK at the proxy
export OPENAI_BASE_URL=http://localhost:4319/openai/v1
```

## Features

**Budget enforcement** — Per-run, daily, and monthly cost caps. Checked before the LLM response reaches your code.

**Kill switches** — Automatic halt when spend exceeds limits. Raises `BudgetExceeded` immediately.

**Approval gates** — Pause execution for human review. Slack, webhook, terminal, or custom handlers.

**Streaming support** — Tracks cost from streaming responses. Auto-injects `stream_options.include_usage` for OpenAI.

**Tool tracking** — Trace MCP tool calls alongside LLM requests. Approval gates on dangerous tools.

**Patch verification** — Runtime health checks confirm interception is active. Configurable failure modes: block, warn, or allow.

**Transport fallback** — Falls back to httpx-level interception when SDK patching fails.

**Proxy mode** — Budget enforcement via reverse proxy. Zero code changes — just set an env var.

## Supported providers

| Provider | Models | Streaming |
|----------|--------|-----------|
| OpenAI | GPT-4o, o1, o3, o4-mini, ... | Yes |
| Anthropic | Claude Opus, Sonnet, Haiku | Yes |
| Google | Gemini 2.0 Pro, Flash, ... | Yes |

43 models tracked with real-time pricing. Unknown models use conservative fallback estimates.

## How it works

Wickd intercepts LLM SDK calls at the method level. When your agent calls `openai.chat.completions.create()`, Wickd's wrapper runs first — checks the budget, forwards the call, tracks the cost, and enforces the cap before returning the response.

```
Your agent code
      |
      v
  Wickd interceptor (budget check, trace)
      |
      v
  OpenAI / Anthropic / Google SDK
      |
      v
  LLM API
```

No separate server. No network hop. No latency added.

## Approval gates

```python
@wickd.approval("delete_user", handler=wickd.webhook_approval_handler("https://..."))
def delete_user(user_id: str):
    db.users.delete(user_id)

@wickd.agent(budget=wickd.Budget(per_run=2.00))
def support_agent(ticket_id: str):
    delete_user("user_123")  # Pauses until human approves
```

## Tool tracking

```python
@wickd.track_tool(name="search_db", server="postgres-mcp")
def search(query: str) -> list:
    return db.search(query)

@wickd.tool_approval(name="send_email", handler=wickd.webhook_approval_handler("https://..."))
def send_email(to: str, body: str):
    mailer.send(to, body)
```

## Health checks

```python
import wickd

status = wickd.status()
# {'patches': {'openai': {'installed': True, 'patched': True, 'verified': True}, ...},
#  'sdk_versions': {'openai': '1.60.0', 'anthropic': '0.42.0'}}
```

## License

MIT
