"""
Wickd Demo: Approval Gates

This demonstrates Wickd's approval gate feature — an agent that
pauses execution and asks for human approval before performing
a sensitive action.

Run: python demo_approval_gate.py
"""

import sys
sys.path.insert(0, "../packages/sdk-python")

import wickd
from wickd.approvals import auto_approve_handler, auto_deny_handler


# A sensitive function protected by an approval gate
@wickd.approval("database_write")
def update_user_email(user_id: str, new_email: str):
    """Simulate updating a user's email in the database."""
    print(f"   📝 Database updated: user {user_id} → {new_email}")
    return {"updated": True, "user_id": user_id, "email": new_email}


# Another sensitive function
@wickd.approval("send_email")
def send_notification(to: str, subject: str):
    """Simulate sending an email to a customer."""
    print(f"   📧 Email sent to {to}: {subject}")
    return {"sent": True, "to": to}


@wickd.agent(
    budget=wickd.Budget(per_run=5.00),
    auto_patch=False,
)
def support_agent(task: str):
    """An agent that needs approval before writing to DB or sending emails."""
    print(f"\n🤖 Agent started: {task}")
    print(f"   This agent will ask for approval before sensitive actions.\n")

    # Step 1: Agent does some processing (no approval needed)
    print("   Step 1: Analysing support ticket... done")

    # Step 2: Agent wants to update the database (APPROVAL NEEDED)
    print("   Step 2: Need to update customer email...")
    result = update_user_email("user_123", "new@example.com")

    # Step 3: Agent wants to send an email (APPROVAL NEEDED)
    print("   Step 3: Need to notify customer...")
    result = send_notification("customer@example.com", "Your email has been updated")

    print("\n   ✅ Agent completed all steps.")


if __name__ == "__main__":
    print("=" * 60)
    print("  WICKD DEMO: Approval Gates")
    print("  The agent will pause and ask for your approval")
    print("  before writing to the database or sending emails.")
    print("  Type 'y' to approve or 'n' to deny each action.")
    print("=" * 60)

    try:
        support_agent.run("Handle support ticket #4521 — customer email change request")
    except wickd.ApprovalDenied as e:
        print(f"\n🛑 Agent stopped: {e}")
        print(f"   The action was denied — agent cannot continue.")
    except wickd.BudgetExceeded as e:
        print(f"\n💀 Agent killed by budget: {e}")

    print("\n✅ Run 'wickd traces' to see the approval events in the trace.\n")
