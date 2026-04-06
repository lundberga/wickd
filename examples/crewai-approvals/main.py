"""
Wickd + CrewAI — Approval gates on agent actions.

Shows how to use Wickd approval gates with CrewAI. The crew runs
normally, but any function decorated with @wickd.approval() pauses
for human approval before executing.

Requirements:
    pip install wickd crewai
    export OPENAI_API_KEY=sk-...

Run:
    python main.py
"""

import wickd


# Protected actions — these require human approval
@wickd.approval("database_write")
def update_customer_record(customer_id: str, updates: dict):
    """Update a customer record in the database."""
    print(f"  DB: Updated customer {customer_id} with {updates}")
    return {"success": True, "customer_id": customer_id}


@wickd.approval("send_email")
def send_customer_email(to: str, subject: str, body: str):
    """Send an email to a customer."""
    print(f"  Email: Sent to {to} — {subject}")
    return {"success": True, "to": to}


@wickd.agent(
    budget=wickd.Budget(per_run=2.00),
    on_budget_kill=wickd.notify.console(),
)
def support_crew(task: str):
    """
    A support agent that processes tickets.

    In a real CrewAI setup, these functions would be tools available
    to your crew. The approval gates work the same way — when the
    crew tries to call a protected function, Wickd pauses for approval.
    """
    print(f"  Processing: {task}")

    # Simulate agent deciding to update the customer record
    # This will pause and ask for approval
    update_customer_record("cust_456", {"email": "new@example.com"})

    # Simulate agent deciding to email the customer
    # This will also pause and ask for approval
    send_customer_email(
        to="new@example.com",
        subject="Your account has been updated",
        body="Hi, we've updated your email address as requested.",
    )

    return "Ticket resolved"


if __name__ == "__main__":
    print("=" * 60)
    print("  WICKD + CREWAI: Approval Gates Demo")
    print("  The agent will ask for your approval before")
    print("  writing to the database or sending emails.")
    print("=" * 60)

    try:
        result = support_crew.run("Handle ticket #789 — customer email change")
        print(f"\nResult: {result}")
    except wickd.ApprovalDenied as e:
        print(f"\nAction denied: {e}")
    except wickd.BudgetExceeded as e:
        print(f"\nBudget exceeded: {e}")

    print("\nRun 'wickd traces' to see approval events in the trace.")
