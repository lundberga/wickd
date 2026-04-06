"""
Wickd + LangGraph — Budget-limited graph agent.

Shows how to add Wickd guardrails to a LangGraph agent. Since Wickd
intercepts the underlying OpenAI/Anthropic SDK calls, it works with
LangGraph without any special integration code.

Requirements:
    pip install wickd langgraph langchain-openai
    export OPENAI_API_KEY=sk-...

Run:
    python main.py
"""

import wickd

# LangGraph imports
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from typing import TypedDict, Annotated
import operator


class AgentState(TypedDict):
    messages: Annotated[list, operator.add]
    task: str
    result: str


def research_node(state: AgentState) -> dict:
    """Research step — calls the LLM."""
    llm = ChatOpenAI(model="gpt-4o-mini")
    response = llm.invoke(f"Research this topic briefly: {state['task']}")
    return {"messages": [response.content], "result": response.content}


def summarise_node(state: AgentState) -> dict:
    """Summarise step — calls the LLM again."""
    llm = ChatOpenAI(model="gpt-4o-mini")
    response = llm.invoke(f"Summarise in 2 sentences: {state['result']}")
    return {"messages": [response.content], "result": response.content}


# Build the graph
graph = StateGraph(AgentState)
graph.add_node("research", research_node)
graph.add_node("summarise", summarise_node)
graph.set_entry_point("research")
graph.add_edge("research", "summarise")
graph.add_edge("summarise", END)
app = graph.compile()


@wickd.agent(
    budget=wickd.Budget(per_run=0.50, daily=5.00),
    on_budget_kill=wickd.notify.console(),
)
def langgraph_agent(task: str):
    """Run the LangGraph workflow with Wickd guardrails."""
    result = app.invoke({"task": task, "messages": [], "result": ""})
    return result["result"]


if __name__ == "__main__":
    try:
        result = langgraph_agent.run("quantum computing applications")
        print(f"\nResult: {result[:200]}")
    except wickd.BudgetExceeded as e:
        print(f"\nBudget exceeded: {e}")

    print("\nRun 'wickd traces' to see per-call cost breakdown.")
    print("Each LangGraph LLM call is individually tracked.")
