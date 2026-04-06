"""
Wickd + Anthropic SDK — Budget-limited agent.

This example shows Wickd automatically intercepting Anthropic API calls
and enforcing budget limits. Zero changes to your existing Claude code.

Requirements:
    pip install wickd anthropic
    export ANTHROPIC_API_KEY=sk-ant-...

Run:
    python main.py
"""

import wickd
import anthropic


@wickd.agent(
    budget=wickd.Budget(per_run=1.00, daily=10.00),
    on_budget_kill=wickd.notify.console(),
)
def writing_agent(task: str):
    """An agent that drafts and refines text using Claude."""
    client = anthropic.Anthropic()

    # Step 1: First draft
    draft = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[
            {"role": "user", "content": f"Write a first draft of: {task}"}
        ],
    )
    draft_text = draft.content[0].text
    print(f"  Draft: {draft_text[:100]}...")

    # Step 2: Refine
    refined = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[
            {"role": "user", "content": f"Improve this text, make it more concise:\n\n{draft_text}"}
        ],
    )
    final_text = refined.content[0].text
    print(f"  Refined: {final_text[:100]}...")

    return final_text


if __name__ == "__main__":
    try:
        result = writing_agent.run("a product description for an AI safety SDK")
        print(f"\nAgent completed. Output length: {len(result)} chars")
    except wickd.BudgetExceeded as e:
        print(f"\nBudget exceeded: {e}")

    print("Run 'wickd traces' to see the full cost breakdown.")
