"""
Wickd + OpenAI SDK — Budget-limited agent.

This example shows how Wickd automatically intercepts OpenAI API calls
and enforces a budget cap. No changes to your OpenAI code needed.

Requirements:
    pip install wickd openai
    export OPENAI_API_KEY=sk-...

Run:
    python main.py
"""

import wickd
import openai


@wickd.agent(
    budget=wickd.Budget(per_run=0.50, daily=5.00),
    on_budget_kill=wickd.notify.console(),
)
def research_agent(task: str):
    """A simple research agent that summarises a topic."""
    client = openai.OpenAI()

    # Step 1: Get an overview
    overview = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a concise research assistant."},
            {"role": "user", "content": f"Give a brief overview of: {task}"},
        ],
    )
    print(f"  Overview: {overview.choices[0].message.content[:100]}...")

    # Step 2: Get key points
    details = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a concise research assistant."},
            {"role": "user", "content": f"List 3 key points about: {task}"},
        ],
    )
    print(f"  Details: {details.choices[0].message.content[:100]}...")

    return {
        "overview": overview.choices[0].message.content,
        "details": details.choices[0].message.content,
    }


if __name__ == "__main__":
    try:
        result = research_agent.run("the history of the internet")
        print(f"\nAgent completed successfully.")
    except wickd.BudgetExceeded as e:
        print(f"\nBudget exceeded: {e}")

    print("Run 'wickd traces' to see the full cost breakdown.")
