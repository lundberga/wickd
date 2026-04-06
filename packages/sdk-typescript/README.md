# wickd

Budget limits, kill switches, and approval gates for AI agents.

## Install

```bash
npm install wickd
```

## Quick start

```typescript
import { agent, Budget, notify } from "wickd";
import OpenAI from "openai";

const myAgent = agent({
  fn: async (task: string) => {
    const client = new OpenAI();
    const response = await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: task }],
    });
    return response.choices[0].message.content;
  },
  budget: new Budget({ perRun: 2.0, daily: 20.0 }),
  onBudgetKill: notify.console(),
});

const result = await myAgent.run("Summarise yesterday's support tickets");
```

One wrapper. Your agent now has a hard cost cap, a full trace of every LLM call, and a summary printed after every run:

```
[wickd] ✓ my_agent — $0.0043 cost | 1 calls | budget: $0.0043/$2.00 | 1203ms | trace: a1b2c3d4
```

## Budget enforcement

Hard cost ceilings, checked in real time inside every LLM call.

```typescript
const myAgent = agent({
  fn: processInvoices,
  budget: new Budget({
    perRun: 2.0,     // kill if this run exceeds $2
    daily: 20.0,     // kill if total today exceeds $20
    monthly: 500.0,  // kill if total this month exceeds $500
  }),
  onBudgetKill: notify.slack("https://hooks.slack.com/..."),
});
```

When a cap is hit, Wickd throws `BudgetExceeded`, saves the full trace, and fires your notification handler.

```typescript
import { BudgetExceeded } from "wickd";

try {
  await myAgent.run("Process all invoices");
} catch (e) {
  if (e instanceof BudgetExceeded) {
    console.log(`Killed at $${e.spent.toFixed(2)}`);
  }
}
```

## Approval gates

Pause execution at sensitive checkpoints and wait for a human.

```typescript
import { agent, Budget, approvalGate } from "wickd";

const updateUser = approvalGate("database_write", (userId: string, data: Record<string, unknown>) => {
  db.update(userId, data);
});

const sendEmail = approvalGate("send_email", (to: string, subject: string, body: string) => {
  email.send(to, subject, body);
});

const supportAgent = agent({
  fn: async (task: string) => {
    // pauses here and asks for approval
    await updateUser("user_123", { email: "new@example.com" });
    await sendEmail("user@example.com", "Update", "Your email was changed");
  },
  budget: new Budget({ perRun: 5.0 }),
});
```

For headless environments, use the webhook handler:

```typescript
import { approvalGate, webhookApprovalHandler } from "wickd";

const handler = webhookApprovalHandler("https://your-api.com/approvals", {
  timeout: 300_000,
  pollInterval: 2000,
});

const riskyAction = approvalGate("deploy", deployToProduction, handler);
```

## Notifications

```typescript
import { notify } from "wickd";

// stderr (local dev)
onBudgetKill: notify.console()

// Slack incoming webhook
onBudgetKill: notify.slack("https://hooks.slack.com/...")

// Generic webhook (JSON POST)
onBudgetKill: notify.webhook("https://your-api.com/alerts")

// Authenticated webhook
onBudgetKill: notify.webhook("https://your-api.com/alerts", {
  Authorization: "Bearer sk-...",
})
```

Multiple handlers:

```typescript
const myAgent = agent({
  fn: myAgentFn,
  budget: new Budget({ perRun: 2.0 }),
  notify: [notify.console(), notify.slack("https://hooks.slack.com/...")],
  onBudgetKill: notify.webhook("https://pagerduty.com/..."),
  onRunComplete: notify.slack("https://hooks.slack.com/..."),
});
```

## Framework compatibility

Wickd patches LLM SDKs at the transport layer, so it works with anything built on top of them -- Vercel AI SDK, LangChain.js, LangGraph, OpenAI Agents SDK. No framework-specific code needed.

```typescript
import { agent, Budget } from "wickd";
import { generateText } from "ai";

const myAgent = agent({
  fn: async (task: string) => {
    const { text } = await generateText({
      model: openai("gpt-4o"),
      prompt: task,
    });
    return text;
  },
  budget: new Budget({ perRun: 1.0 }),
});
```

## How it works

1. `agent()` wraps your function and patches the OpenAI, Anthropic, and Google GenAI SDKs
2. Every LLM call is intercepted -- tokens and cost are tracked in real time
3. If cost exceeds the budget, execution is killed immediately (`BudgetExceeded`)
4. `approvalGate()` functions pause for human approval before running
5. A full trace is saved to `~/.wickd/traces/` after every run

Everything runs locally. No data leaves your machine.

## API

### `agent(options)`

| Option | Type | Description |
|--------|------|-------------|
| `fn` | `(...args) => any` | The agent function to wrap |
| `budget` | `Budget` | Budget limits (perRun, daily, monthly) |
| `name` | `string` | Agent name for traces (defaults to function name) |
| `onBudgetKill` | `NotifyHandler` | Called when budget is exceeded |
| `onRunComplete` | `NotifyHandler` | Called after every run |
| `notify` | `NotifyHandler[]` | Handlers for all notification events |
| `autoPatch` | `boolean` | Auto-patch LLM SDKs (default: `true`) |

### `Budget`

```typescript
new Budget({ perRun: 2.0, daily: 20.0, monthly: 500.0 })
```

### `approvalGate(name, fn, handler?)`

Wraps a function with a human approval checkpoint. Returns an async version of the function.

### Manual patching

```typescript
import { patchOpenAI, patchAnthropic, patchGoogle, patchAll } from "wickd";

patchOpenAI();
patchAnthropic();
patchGoogle();
patchAll();       // all of the above
```

## License

MIT
