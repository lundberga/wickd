"""wickd init subcommand."""

import json
import sys
from pathlib import Path

_AGENT_TEMPLATE = '''\
import wickd


@wickd.approval("database_write")
def update_record(record_id: str, data: dict):
    """Protected action -- requires approval."""
    print(f"  Updated record {record_id}")
    return {"updated": True}


@wickd.agent(
    budget=wickd.Budget(per_run=2.00, daily=20.00),
    on_budget_kill=wickd.notify.console(),
)
def my_agent(task: str):
    # Wickd intercepts OpenAI/Anthropic/Google SDK calls automatically.
    # Just use them normally:
    #
    # import openai
    # client = openai.OpenAI()
    # response = client.chat.completions.create(
    #     model="gpt-4o",
    #     messages=[{"role": "user", "content": task}],
    # )

    print(f"Agent running: {task}")
    return "done"


if __name__ == "__main__":
    import sys
    task = " ".join(sys.argv[1:]) or "Hello from Wickd"
    try:
        result = my_agent.run(task)
        print(f"Result: {result}")
    except wickd.BudgetExceeded as e:
        print(f"Budget killed: {e}")
    except wickd.ApprovalDenied as e:
        print(f"Approval denied: {e}")
'''

_CONFIG_TEMPLATE = {
    "version": "0.1.0",
    "defaults": {
        "budget": {"per_run": 2.00, "daily": 20.00},
        "approvals": ["database_write", "send_email", "api_call_with_payment"],
        "notifications": {
            "console": True,
            "slack_webhook": "",
        },
    },
}


def cmd_init(args: object) -> None:
    """Scaffold a Wickd agent and config in the current directory."""
    agent_file = Path("wickd_agent.py")
    config_file = Path("wickd.json")
    created = []

    if not agent_file.exists():
        agent_file.write_text(_AGENT_TEMPLATE)
        created.append(str(agent_file))

    if not config_file.exists():
        config_file.write_text(json.dumps(_CONFIG_TEMPLATE, indent=2) + "\n")
        created.append(str(config_file))

    if created:
        print(f"Created: {', '.join(created)}")
        print(f"\nNext steps:")
        print(f"  1. Edit {agent_file} with your agent logic")
        print(f"  2. Run: python {agent_file} \"Your task here\"")
        print(f"  3. View traces: wickd traces")
    else:
        print("Files already exist.", file=sys.stderr)
