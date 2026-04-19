"""
Auto-patch Model Context Protocol (MCP) client sessions.

When an agent uses `mcp.ClientSession.call_tool()`, Wickd:
  1. Records the call in the active trace with latency, input preview,
     output preview, and the MCP server name (when discoverable).
  2. Gates the call behind human approval if the tool name is in the
     agent's approval allowlist.

Graceful no-op when the `mcp` package is not installed.
"""

import asyncio
import logging
import time
from contextvars import ContextVar
from typing import Any, Callable, Optional

logger = logging.getLogger("wickd")

from wickd.interceptor import get_active_trace, _record_tool_call_on_tracker
from wickd.approvals import ApprovalDenied, _terminal_handler
from wickd.budget import BudgetExceeded

_SENTINEL = "_wickd_mcp_patched"
MAX_PREVIEW = 200

# Per-run approval configuration, propagated via ContextVar so concurrent
# agent runs stay isolated.
_approval_required: ContextVar[frozenset] = ContextVar(
    "wickd_mcp_approval_required", default=frozenset()
)
_approval_handler: ContextVar[Optional[Callable]] = ContextVar(
    "wickd_mcp_approval_handler", default=None
)

_patch_status: dict = {
    "installed": False,
    "patched": False,
    "verified": False,
    "error": None,
}


def set_mcp_approval_config(
    tool_names: Optional[list] = None,
    handler: Optional[Callable] = None,
) -> tuple:
    """Set MCP approval config for the current context. Returns reset tokens."""
    names = frozenset(tool_names or [])
    req_token = _approval_required.set(names)
    handler_token = _approval_handler.set(handler)
    return (req_token, handler_token)


def reset_mcp_approval_config(tokens: tuple) -> None:
    """Restore previous approval config using tokens from set_mcp_approval_config."""
    req_token, handler_token = tokens
    _approval_required.reset(req_token)
    _approval_handler.reset(handler_token)


def _server_name(session) -> Optional[str]:
    """Best-effort extraction of the MCP server name from a session."""
    for attr in ("server_info", "_server_info"):
        info = getattr(session, attr, None)
        if info is not None:
            name = getattr(info, "name", None)
            if name:
                return str(name)
    override = getattr(session, "_wickd_server_name", None)
    if override:
        return str(override)
    return None


def _preview(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)[:MAX_PREVIEW]
    except Exception:
        return "<unrepresentable>"


def _output_preview(result: Any) -> str:
    """Render a CallToolResult-like object as a short string."""
    if result is None:
        return ""
    content = getattr(result, "content", None)
    if content is None:
        return _preview(result)
    try:
        parts = []
        for item in content:
            text = getattr(item, "text", None)
            parts.append(str(text) if text is not None else repr(item))
        return (" ".join(parts))[:MAX_PREVIEW]
    except Exception:
        return _preview(result)


def _is_error_result(result: Any) -> bool:
    return bool(getattr(result, "isError", False))


async def _invoke_handler(handler: Callable, context: dict) -> bool:
    """Run sync or async approval handler without blocking the event loop."""
    if asyncio.iscoroutinefunction(handler):
        return bool(await handler(context))
    return bool(await asyncio.to_thread(handler, context))


async def _run_approval(tool_name: str, arguments: Any, server: Optional[str]) -> bool:
    handler = _approval_handler.get() or _terminal_handler
    trace = get_active_trace()
    context = {
        "name": tool_name,
        "function": f"mcp:{server}:{tool_name}" if server else f"mcp:{tool_name}",
        "args_preview": _preview(arguments),
        "kwargs_preview": "",
        "trace_id": trace.trace_id[:8] if trace else "unknown",
    }
    return await _invoke_handler(handler, context)


def _wrap_call_tool(original: Callable) -> Callable:
    """Build an async wrapper around ClientSession.call_tool."""

    async def wrapper(self, name: str, arguments: Any = None, *args, **kwargs):
        trace = get_active_trace()
        server = _server_name(self)
        required = _approval_required.get()

        if name in required:
            if trace:
                trace.add_approval(f"mcp:{name}", "pending")
            approved = await _run_approval(name, arguments, server)
            if not approved:
                if trace:
                    trace.add_approval(f"mcp:{name}", "denied")
                    trace.add_tool_call(
                        tool_name=name,
                        latency_ms=0,
                        tool_input=_preview(arguments),
                        tool_output="Denied by approver",
                        tool_status="denied",
                        tool_server=server,
                    )
                raise ApprovalDenied(name)
            if trace:
                trace.add_approval(f"mcp:{name}", "approved")

        start = time.time()
        try:
            result = await original(self, name, arguments, *args, **kwargs)
        except BudgetExceeded:
            # Runaway guard tripped inside the call — let it propagate.
            raise
        except Exception as exc:
            if trace:
                trace.add_tool_call(
                    tool_name=name,
                    latency_ms=(time.time() - start) * 1000,
                    tool_input=_preview(arguments),
                    tool_output=str(exc)[:MAX_PREVIEW],
                    tool_status="error",
                    tool_server=server,
                )
            try:
                _record_tool_call_on_tracker()
            except BudgetExceeded:
                # Suppress — the original exception is what the caller asked about.
                pass
            raise

        if trace:
            tool_status = "error" if _is_error_result(result) else "success"
            trace.add_tool_call(
                tool_name=name,
                latency_ms=(time.time() - start) * 1000,
                tool_input=_preview(arguments),
                tool_output=_output_preview(result),
                tool_status=tool_status,
                tool_server=server,
            )
        # Count the invocation on the active run's tracker. May raise
        # BudgetExceeded if max_tool_calls or max_duration is tripped.
        _record_tool_call_on_tracker()
        return result

    setattr(wrapper, _SENTINEL, True)
    return wrapper


def patch_mcp() -> bool:
    """Patch mcp.ClientSession.call_tool for auto-tracking. Idempotent."""
    try:
        from mcp import ClientSession
    except ImportError:
        _patch_status.update(
            installed=False, patched=False, verified=False, error=None
        )
        return False

    _patch_status["installed"] = True
    original = ClientSession.call_tool

    if getattr(original, _SENTINEL, False):
        _patch_status.update(patched=True, verified=True, error=None)
        return True

    try:
        ClientSession._wickd_original_call_tool = original
        ClientSession.call_tool = _wrap_call_tool(original)
        _patch_status.update(patched=True, verified=True, error=None)
        return True
    except Exception as exc:
        logger.warning("wickd: failed to patch mcp.ClientSession: %s", exc)
        _patch_status.update(patched=False, verified=False, error=str(exc))
        return False


def verify_mcp_patch() -> bool:
    """Re-verify the MCP patch is still active."""
    try:
        from mcp import ClientSession
    except ImportError:
        _patch_status["verified"] = False
        return False
    is_patched = bool(getattr(ClientSession.call_tool, _SENTINEL, False))
    _patch_status["patched"] = is_patched
    _patch_status["verified"] = is_patched
    return is_patched


def mcp_patch_status() -> dict:
    """Return the current MCP patch status dict."""
    return dict(_patch_status)


def unpatch_mcp() -> bool:
    """Restore the original ClientSession.call_tool. Primarily for tests."""
    try:
        from mcp import ClientSession
    except ImportError:
        return False
    original = getattr(ClientSession, "_wickd_original_call_tool", None)
    if original is None:
        return False
    ClientSession.call_tool = original
    delattr(ClientSession, "_wickd_original_call_tool")
    _patch_status.update(patched=False, verified=False)
    return True
