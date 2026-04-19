"""
Wickd + MCP: auto-tracking tool calls with an approval gate on destructive ops.

Run:
    pip install wickd-ai mcp
    python main.py

This example uses a stub MCP server (in-process) to keep the demo hermetic.
In real use, you would connect to an MCP server over stdio/HTTP and Wickd
would auto-track every `session.call_tool(...)` call inside your agent.
"""

import asyncio
import sys
import types

import wickd


# ── Stub MCP server (for a self-contained demo) ────────────────────────────
#
# In a real app you'd use the official `mcp` package to connect over stdio:
#
#     from mcp import ClientSession
#     from mcp.client.stdio import stdio_client
#
#     async with stdio_client(StdioServerParameters(...)) as (r, w):
#         async with ClientSession(r, w) as session:
#             await session.initialize()
#             result = await session.call_tool("search", {"q": "wickd"})
#
# Wickd patches `ClientSession.call_tool` at the class level, so every call
# an agent makes is tracked automatically.
#
# This stub shims the same API shape so the example runs without a real server.

class _ServerInfo:
    def __init__(self, name): self.name = name

class _Content:
    def __init__(self, text): self.text = text

class _CallToolResult:
    def __init__(self, content, isError=False):
        self.content = content
        self.isError = isError

def _install_stub_mcp():
    module = types.ModuleType("mcp")

    class StubClientSession:
        def __init__(self, server_name):
            self.server_info = _ServerInfo(server_name)

        async def call_tool(self, name, arguments=None):
            if name == "search_docs":
                await asyncio.sleep(0.05)
                return _CallToolResult([_Content(f"3 results for '{arguments.get('q')}'")])
            if name == "drop_table":
                return _CallToolResult(
                    [_Content(f"table {arguments.get('table')} dropped")]
                )
            return _CallToolResult([_Content("unknown tool")], isError=True)

    module.ClientSession = StubClientSession
    module.__version__ = "0.0.0-stub"
    sys.modules["mcp"] = module


# ── The agent ──────────────────────────────────────────────────────────────

_install_stub_mcp()
from mcp import ClientSession


@wickd.agent(
    budget=wickd.Budget(per_run=1.00),
    # Gate any MCP tool named `drop_table` behind a human-approval handler.
    mcp_approval_required=["drop_table"],
    mcp_approval_handler=wickd.auto_approve_handler,  # terminal handler in real use
    on_patch_failure="allow",
)
async def research_agent(topic: str):
    session = ClientSession("docs-mcp")
    search = await session.call_tool("search_docs", {"q": topic})
    print(f"  search result: {search.content[0].text}")

    # This one is gated. Approved here via auto_approve_handler; in production
    # you would use `wickd.webhook_approval_handler(...)` or the default
    # terminal handler.
    drop = await session.call_tool("drop_table", {"table": "stale_cache"})
    print(f"  drop result:   {drop.content[0].text}")

    return "done"


async def main():
    print("Running agent...")
    result = await research_agent.arun("model context protocol")
    print(f"\nagent returned: {result}\n")

    trace = research_agent.trace_store.list_traces()[0]  # newest first
    print("Tool calls recorded in the trace:")
    for event in trace["events"]:
        if event["event_type"] == "tool_call":
            print(
                f"  - {event['tool_name']:15s} "
                f"server={event.get('tool_server', '-'):10s} "
                f"status={event['tool_status']:8s} "
                f"latency={event.get('latency_ms', 0):.1f}ms"
            )


if __name__ == "__main__":
    asyncio.run(main())
