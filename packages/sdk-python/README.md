# wickd

Budget limits, kill switches, and human approval gates for AI agents.

## Install

```bash
pip install wickd
```

## Quick start

```python
import wickd

@wickd.agent(budget=wickd.Budget(per_run=2.00, daily=20.00))
def my_agent(task: str):
    import openai
    client = openai.OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": task}]
    )
    return response.choices[0].message.content

my_agent.run("Summarise yesterday's support tickets")
```

One decorator. Your agent now has a hard cost cap, full run tracing, and a summary after every run:

```
[wickd] ✓ my_agent | $0.0043 cost | 1 calls | budget: $0.0043/$2.00 | 1203ms | trace: a1b2c3d4
```

## Budget enforcement

Hard cost ceilings, enforced in real time. When a cap is hit, execution stops immediately.

```python
@wickd.agent(
    budget=wickd.Budget(
        per_run=2.00,    # kill if this run exceeds $2
        daily=20.00,     # kill if today's total exceeds $20
        monthly=500.00,  # kill if this month exceeds $500
    ),
    on_budget_kill=wickd.notify.slack("https://hooks.slack.com/..."),
)
def my_agent(task: str):
    ...
```

```python
try:
    my_agent.run("Process all invoices")
except wickd.BudgetExceeded as e:
    print(f"Killed at ${e.spent:.2f}")
```

## Approval gates

Pause execution at sensitive checkpoints and wait for human approval.

```python
@wickd.approval("database_write")
def update_user_record(user_id, data):
    db.update(user_id, data)

@wickd.agent(budget=wickd.Budget(per_run=5.00))
def support_agent(task: str):
    # ...agent logic...
    update_user_record("user_123", {"email": "new@example.com"})  # pauses here
```

The agent pauses, prompts for approve/deny, and continues or aborts.

## Traces

Every run is traced automatically.

```bash
wickd traces            # list recent
wickd traces --cost     # sort by cost
wickd traces a1b2c3d4   # detail view
```

## Notifications

```python
on_budget_kill=wickd.notify.console()                              # local dev
on_budget_kill=wickd.notify.slack("https://hooks.slack.com/...")    # slack
on_budget_kill=wickd.notify.webhook("https://your-api.com/alerts") # any webhook
```

## Supported SDKs

Wickd intercepts calls from OpenAI, Anthropic, and Google GenAI SDKs automatically. No code changes needed beyond the `@wickd.agent()` decorator.

## How it works

1. `@wickd.agent()` wraps your function
2. Wickd patches the LLM SDKs to intercept every API call
3. Token usage and cost are tracked in real time
4. If cost exceeds the budget, execution is killed immediately
5. `@wickd.approval()` functions pause for human approval
6. A full trace is saved locally after every run

Everything runs locally. No data leaves your machine.

## License

MIT
