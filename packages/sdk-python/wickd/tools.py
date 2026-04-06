"""
Tool call tracking and approval gates for MCP servers and custom tools.

Decorators and utilities to trace tool executions inside agent runs,
enforce approval gates on dangerous operations, and record tool usage.
Works with any tool framework — MCP, LangChain tools, or plain functions.
"""

import asyncio
import functools
import time
from typing import Callable, Optional, Any

from wickd.interceptor import get_active_trace
from wickd.approvals import ApprovalGate, ApprovalDenied

MAX_PREVIEW = 200


def _build_input_preview(args, kwargs) -> str:
    parts = []
    if args:
        parts.append(str(args)[:100])
    if kwargs:
        parts.append(str(kwargs)[:100])
    return ", ".join(parts)


def _trace_tool(tool_name: str, server: Optional[str], latency_ms: float,
                input_preview: str, output: Any, status: str):
    trace = get_active_trace()
    if trace:
        output_preview = str(output)[:MAX_PREVIEW] if output is not None else ""
        trace.add_tool_call(
            tool_name=tool_name, latency_ms=round(latency_ms, 1),
            tool_input=input_preview, tool_output=output_preview,
            tool_status=status, tool_server=server,
        )


def track_tool(name: Optional[str] = None, server: Optional[str] = None) -> Callable:
    """Decorator: record a function as a tool call in the active Wickd trace.

    Usage::

        @wickd.track_tool(name="search_db", server="postgres-mcp")
        def search(query: str) -> list:
            return db.search(query)
    """
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            preview = _build_input_preview(args, kwargs)
            start = time.time()
            try:
                result = fn(*args, **kwargs)
                _trace_tool(tool_name, server, (time.time() - start) * 1000, preview, result, "success")
                return result
            except Exception as exc:
                _trace_tool(tool_name, server, (time.time() - start) * 1000, preview, exc, "error")
                raise

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            preview = _build_input_preview(args, kwargs)
            start = time.time()
            try:
                result = await fn(*args, **kwargs)
                _trace_tool(tool_name, server, (time.time() - start) * 1000, preview, result, "success")
                return result
            except Exception as exc:
                _trace_tool(tool_name, server, (time.time() - start) * 1000, preview, exc, "error")
                raise

        return async_wrapper if asyncio.iscoroutinefunction(fn) else sync_wrapper
    return decorator


def tool_approval(
    name: Optional[str] = None,
    handler: Optional[Callable] = None,
    server: Optional[str] = None,
    timeout: float = 300,
) -> Callable:
    """Decorator: gate a tool behind human approval, then track the call.

    On approval the tool runs and is recorded. On denial, ApprovalDenied is
    raised and a denied tool call is recorded in the trace.

    Usage::

        @wickd.tool_approval(name="delete_user", handler=wickd.webhook_approval_handler("..."))
        def delete_user(user_id: str):
            db.delete(user_id)
    """
    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        tracked = track_tool(name=tool_name, server=server)(fn)
        gate = ApprovalGate(fn=tracked, name=tool_name, handler=handler, timeout=timeout)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return gate(*args, **kwargs)
            except ApprovalDenied:
                _trace_tool(tool_name, server, 0,
                            str(args)[:100] if args else "", "Denied by approver", "denied")
                raise

        return wrapper
    return decorator


def record_tool_call(
    tool_name: str,
    tool_input: Any = None,
    tool_output: Any = None,
    latency_ms: float = 0,
    tool_status: str = "success",
    server: Optional[str] = None,
):
    """Manually record a tool call in the active trace.

    Use when you can't decorate the tool function directly — e.g. calls
    to external MCP servers or third-party tool frameworks.
    """
    trace = get_active_trace()
    if trace:
        trace.add_tool_call(
            tool_name=tool_name,
            latency_ms=round(latency_ms, 1),
            tool_input=str(tool_input)[:MAX_PREVIEW] if tool_input is not None else "",
            tool_output=str(tool_output)[:MAX_PREVIEW] if tool_output is not None else "",
            tool_status=tool_status,
            tool_server=server,
        )
