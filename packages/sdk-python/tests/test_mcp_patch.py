"""Tests for MCP ClientSession.call_tool auto-patching.

Uses a fake `mcp` module injected into sys.modules so tests run without
depending on the real Anthropic modelcontextprotocol python SDK.
"""

import asyncio
import sys
import tempfile
import types
from unittest.mock import MagicMock

import pytest


# ── Fake mcp module ────────────────────────────────────────────────────────

class _FakeServerInfo:
    def __init__(self, name: str):
        self.name = name


class _FakeContent:
    def __init__(self, text: str):
        self.text = text


class _FakeCallToolResult:
    def __init__(self, content=None, isError: bool = False):
        self.content = content or []
        self.isError = isError


class FakeClientSession:
    """Stand-in for mcp.ClientSession.

    Tests replace `_impl` to control behaviour. The instance method
    `call_tool` is what Wickd patches at the class level.
    """

    def __init__(self, server_name: str = None, impl=None):
        if server_name:
            self.server_info = _FakeServerInfo(server_name)
        self._impl = impl or self._default_impl

    @staticmethod
    async def _default_impl(name, arguments):
        return _FakeCallToolResult(content=[_FakeContent(f"ok:{name}")])

    async def call_tool(self, name, arguments=None):
        return await self._impl(name, arguments)


def _install_fake_mcp():
    """Inject a fake `mcp` module into sys.modules and reload the patcher."""
    module = types.ModuleType("mcp")
    module.ClientSession = FakeClientSession
    module.__version__ = "0.0.0-fake"
    sys.modules["mcp"] = module
    return module


def _uninstall_fake_mcp():
    sys.modules.pop("mcp", None)


@pytest.fixture
def fake_mcp():
    """Install fake mcp module and a fresh FakeClientSession per test."""
    # Ensure clean state first: unpatch any previous instrumentation.
    try:
        from wickd.mcp_patch import unpatch_mcp
        unpatch_mcp()
    except Exception:
        pass
    _uninstall_fake_mcp()

    # Rebuild the fake class each test so class-level patches don't leak.
    global FakeClientSession

    class FreshSession:
        def __init__(self, server_name=None, impl=None):
            if server_name:
                self.server_info = _FakeServerInfo(server_name)
            self._impl = impl or FreshSession._default_impl

        @staticmethod
        async def _default_impl(name, arguments):
            return _FakeCallToolResult(content=[_FakeContent(f"ok:{name}")])

        async def call_tool(self, name, arguments=None):
            return await self._impl(name, arguments)

    module = types.ModuleType("mcp")
    module.ClientSession = FreshSession
    module.__version__ = "0.0.0-fake"
    sys.modules["mcp"] = module

    yield module

    try:
        from wickd.mcp_patch import unpatch_mcp
        unpatch_mcp()
    except Exception:
        pass
    _uninstall_fake_mcp()


# ── Helpers ────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# ── Tests: patching mechanics ──────────────────────────────────────────────

class TestPatchMcp:
    def test_patch_sets_sentinel(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp, verify_mcp_patch

        assert patch_mcp() is True
        assert verify_mcp_patch() is True

    def test_patch_is_idempotent(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp

        assert patch_mcp() is True
        # Patching again should be a no-op, not double-wrap.
        assert patch_mcp() is True

        from mcp import ClientSession
        # Original should still be recoverable.
        assert hasattr(ClientSession, "_wickd_original_call_tool")

    def test_patch_noop_when_mcp_missing(self):
        # Simulate "mcp not installed" even if dev deps have it: setting the
        # module entry to None makes `import mcp` raise ImportError.
        prior = sys.modules.get("mcp", "__missing__")
        sys.modules["mcp"] = None
        # Force re-import of the patcher so it re-evaluates the import.
        sys.modules.pop("wickd.mcp_patch", None)
        try:
            from wickd.mcp_patch import patch_mcp, mcp_patch_status, verify_mcp_patch

            assert patch_mcp() is False
            assert verify_mcp_patch() is False
            status = mcp_patch_status()
            assert status["installed"] is False
            assert status["patched"] is False
        finally:
            if prior == "__missing__":
                sys.modules.pop("mcp", None)
            else:
                sys.modules["mcp"] = prior
            sys.modules.pop("wickd.mcp_patch", None)

    def test_unpatch_restores_original(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp, unpatch_mcp, verify_mcp_patch

        patch_mcp()
        assert verify_mcp_patch() is True

        assert unpatch_mcp() is True
        assert verify_mcp_patch() is False


# ── Tests: call tracking ───────────────────────────────────────────────────

class TestCallToolTracking:
    def test_successful_call_recorded(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)

        try:
            session = fake_mcp.ClientSession(server_name="postgres-mcp")
            result = _run(session.call_tool("query", {"sql": "SELECT 1"}))
            assert result.content[0].text == "ok:query"

            assert len(trace.events) == 1
            event = trace.events[0]
            assert event.event_type == "tool_call"
            assert event.tool_name == "query"
            assert event.tool_status == "success"
            assert event.tool_server == "postgres-mcp"
            assert "SELECT 1" in event.tool_input_preview
            assert "ok:query" in event.tool_output_preview
            assert event.latency_ms is not None and event.latency_ms >= 0
        finally:
            set_active_trace(None)

    def test_error_call_recorded(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)

        async def bad_impl(name, args):
            raise RuntimeError("boom")

        try:
            session = fake_mcp.ClientSession(server_name="srv", impl=bad_impl)
            with pytest.raises(RuntimeError, match="boom"):
                _run(session.call_tool("broken", {}))

            assert len(trace.events) == 1
            assert trace.events[0].tool_status == "error"
            assert "boom" in trace.events[0].tool_output_preview
        finally:
            set_active_trace(None)

    def test_tool_error_flag_sets_error_status(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)

        async def error_result_impl(name, args):
            return _FakeCallToolResult(
                content=[_FakeContent("permission denied")], isError=True
            )

        try:
            session = fake_mcp.ClientSession(server_name="srv", impl=error_result_impl)
            _run(session.call_tool("read_file", {"path": "/etc/passwd"}))

            assert trace.events[0].tool_status == "error"
        finally:
            set_active_trace(None)

    def test_no_active_trace_does_not_crash(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp
        from wickd.interceptor import set_active_trace

        patch_mcp()
        set_active_trace(None)

        session = fake_mcp.ClientSession(server_name="srv")
        # Should not raise.
        result = _run(session.call_tool("ping", None))
        assert result.content[0].text == "ok:ping"

    def test_server_name_falls_back_to_none(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)

        try:
            # No server_name set.
            session = fake_mcp.ClientSession()
            _run(session.call_tool("nameless", None))
            assert trace.events[0].tool_server is None
        finally:
            set_active_trace(None)


# ── Tests: approval gating ─────────────────────────────────────────────────

class TestApprovalGating:
    def test_approved_call_proceeds(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp, set_mcp_approval_config, reset_mcp_approval_config
        from wickd.approvals import auto_approve_handler
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)
        tokens = set_mcp_approval_config(["delete_user"], auto_approve_handler)

        try:
            session = fake_mcp.ClientSession(server_name="postgres-mcp")
            result = _run(session.call_tool("delete_user", {"id": 42}))
            assert result.content[0].text == "ok:delete_user"

            # Should have 3 events: approval_pending, approval_approved, tool_call.
            types_seen = [e.event_type for e in trace.events]
            assert types_seen.count("approval_gate") >= 2
            assert "tool_call" in types_seen
            tool_event = next(e for e in trace.events if e.event_type == "tool_call")
            assert tool_event.tool_status == "success"
        finally:
            reset_mcp_approval_config(tokens)
            set_active_trace(None)

    def test_denied_call_raises_and_records(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp, set_mcp_approval_config, reset_mcp_approval_config
        from wickd.approvals import auto_deny_handler, ApprovalDenied
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)
        tokens = set_mcp_approval_config(["delete_user"], auto_deny_handler)

        try:
            session = fake_mcp.ClientSession(server_name="postgres-mcp")
            with pytest.raises(ApprovalDenied):
                _run(session.call_tool("delete_user", {"id": 42}))

            tool_events = [e for e in trace.events if e.event_type == "tool_call"]
            assert len(tool_events) == 1
            assert tool_events[0].tool_status == "denied"
        finally:
            reset_mcp_approval_config(tokens)
            set_active_trace(None)

    def test_non_listed_tool_bypasses_gate(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp, set_mcp_approval_config, reset_mcp_approval_config
        from wickd.approvals import auto_deny_handler
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)
        # Only delete_user requires approval, not `read_file`.
        tokens = set_mcp_approval_config(["delete_user"], auto_deny_handler)

        try:
            session = fake_mcp.ClientSession(server_name="srv")
            result = _run(session.call_tool("read_file", {"path": "/tmp/x"}))
            assert result.content[0].text == "ok:read_file"

            # No approval events should exist.
            approval_events = [
                e for e in trace.events if e.event_type == "approval_gate"
            ]
            assert approval_events == []
        finally:
            reset_mcp_approval_config(tokens)
            set_active_trace(None)

    def test_async_handler_supported(self, fake_mcp):
        from wickd.mcp_patch import patch_mcp, set_mcp_approval_config, reset_mcp_approval_config
        from wickd.trace import Trace
        from wickd.interceptor import set_active_trace

        patch_mcp()
        trace = Trace(agent_name="t", task="t")
        set_active_trace(trace)

        async def async_approve(context):
            return True

        tokens = set_mcp_approval_config(["delete_user"], async_approve)

        try:
            session = fake_mcp.ClientSession(server_name="srv")
            result = _run(session.call_tool("delete_user", {"id": 1}))
            assert result.content[0].text == "ok:delete_user"
        finally:
            reset_mcp_approval_config(tokens)
            set_active_trace(None)


# ── Tests: agent integration ───────────────────────────────────────────────

class TestAgentIntegration:
    def test_agent_auto_patches_mcp(self, fake_mcp):
        from wickd.agent import agent
        from wickd.mcp_patch import verify_mcp_patch

        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(trace_dir=tmpdir, on_patch_failure="allow")
            async def my_agent(task):
                session = fake_mcp.ClientSession(server_name="srv")
                result = await session.call_tool("search", {"q": task})
                return result.content[0].text

            assert verify_mcp_patch() is True
            out = asyncio.run(my_agent.arun("hello"))
            assert out == "ok:search"

            traces = my_agent.trace_store.list_traces()
            assert len(traces) == 1
            tool_events = [e for e in traces[0]["events"] if e["event_type"] == "tool_call"]
            assert len(tool_events) == 1
            assert tool_events[0]["tool_name"] == "search"
            assert tool_events[0]["tool_server"] == "srv"

    def test_agent_mcp_approval_denies(self, fake_mcp):
        from wickd.agent import agent
        from wickd.approvals import auto_deny_handler, ApprovalDenied

        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(
                trace_dir=tmpdir,
                on_patch_failure="allow",
                mcp_approval_required=["drop_table"],
                mcp_approval_handler=auto_deny_handler,
            )
            async def destructive(task):
                session = fake_mcp.ClientSession(server_name="db")
                return await session.call_tool("drop_table", {"table": task})

            with pytest.raises(ApprovalDenied):
                asyncio.run(destructive.arun("users"))

    def test_agent_config_isolated_between_runs(self, fake_mcp):
        """Agent A's approval config must not leak into Agent B."""
        from wickd.agent import agent
        from wickd.approvals import auto_deny_handler

        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(
                trace_dir=tmpdir,
                on_patch_failure="allow",
                mcp_approval_required=["dangerous"],
                mcp_approval_handler=auto_deny_handler,
            )
            async def guarded(task):
                session = fake_mcp.ClientSession(server_name="srv")
                return await session.call_tool("safe_tool", {})

            @agent(trace_dir=tmpdir, on_patch_failure="allow")
            async def unguarded(task):
                session = fake_mcp.ClientSession(server_name="srv")
                return await session.call_tool("dangerous", {})

            # Run guarded first (approval config set), then unguarded.
            asyncio.run(guarded.arun("x"))
            # unguarded has no approval config — "dangerous" should pass.
            result = asyncio.run(unguarded.arun("x"))
            assert result.content[0].text == "ok:dangerous"
