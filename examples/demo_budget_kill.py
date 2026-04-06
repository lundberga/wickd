"""
Wickd Demo: Budget Kill Switch

This demonstrates Wickd's core feature — an agent that gets killed
when it exceeds its budget. No real API calls needed; we simulate
LLM calls to show the budget enforcement working.

Run: python demo_budget_kill.py
"""

import sys
sys.path.insert(0, "../packages/sdk-python")

import wickd
from wickd.budget import BudgetTracker
from wickd.interceptor import set_active_tracker, set_active_trace
from wickd.trace import Trace


# Simulate LLM calls (for demo without API keys)
def fake_llm_call(prompt: str, tracker: BudgetTracker, trace: Trace):
    """Simulate an LLM call that costs ~$0.30 each time."""
    model = "gpt-4o"
    input_tokens = 500
    output_tokens = 200
    cost = wickd.pricing.calculate_cost(model, input_tokens, output_tokens)

    # Record in trace
    trace.add_llm_call(
        model=model,
        provider="openai",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
        latency_ms=150.0,
        prompt_preview=prompt,
        response_preview="[simulated response]",
    )

    # Record cost — this is where budget enforcement happens
    tracker.record_cost(cost, model, input_tokens, output_tokens)

    return f"Response to: {prompt}"


@wickd.agent(
    budget=wickd.Budget(per_run=0.01),  # Very low budget to trigger kill quickly
    on_budget_kill=wickd.notify.console("[demo]"),
    auto_patch=False,  # Don't try to patch real SDKs for this demo
)
def demo_agent(task: str):
    """An agent that makes LLM calls until budget kills it."""
    print(f"\n🤖 Agent started: {task}")
    print(f"   Budget: $0.01 per run\n")

    # Access the tracker and trace from the agent internals
    tracker = demo_agent._tracker
    trace = demo_agent._trace

    for i in range(10):
        remaining = tracker.remaining()
        print(f"   Call {i+1}: making LLM call... (${tracker.run_spend:.4f} spent, ${remaining:.4f} remaining)")

        # This call will eventually trigger BudgetExceeded
        result = fake_llm_call(f"Step {i+1} of task: {task}", tracker, trace)
        print(f"   Call {i+1}: ✓ got response")

    print("   Agent finished all calls (budget was not hit)")


if __name__ == "__main__":
    print("=" * 60)
    print("  WICKD DEMO: Budget Kill Switch")
    print("  The agent has a $0.01 budget but each call costs ~$0.003")
    print("  Watch it get killed after a few calls.")
    print("=" * 60)

    try:
        demo_agent.run("Summarise today's support tickets")
    except wickd.BudgetExceeded as e:
        print(f"\n💀 Agent was killed by Wickd: {e}")
        print(f"   This is exactly what should happen — budget enforcement works!")

    print("\n✅ Run 'wickd traces' to see the full trace of this run.\n")
