"""Tests for MCP tool tracking, tool approval, and record_tool_call."""

import tempfile
import time
import pytest
from wickd.agent import agent, WickdAgent
from wickd.budget import Budget
from wickd.trace import Trace, TraceEvent
from wickd.tools import track_tool, tool_approval, record_tool_call
from wickd.approvals import ApprovalDenied, auto_approve_handler, auto_deny_handler
from wickd.interceptor import set_active_trace, set_active_tracker


class TestTraceToolCall:
    def test_add_tool_call(self):
        trace = Trace(agent_name="test", task="test")
        event = trace.add_tool_call(
            tool_name="search_db",
            latency_ms=42.5,
            tool_input='{"query": "test"}',
            tool_output='[{"id": 1}]',
            tool_status="success",
            tool_server="postgres-mcp",
        )
        assert event.event_type == "tool_call"
        assert event.tool_name == "search_db"
        assert event.latency_ms == 42.5
        assert event.tool_status == "success"
        assert event.tool_server == "postgres-mcp"
        assert event.metadata["tool"] == "search_db"
        assert event.metadata["server"] == "postgres-mcp"

    def test_tool_call_in_trace_dict(self):
        trace = Trace(agent_name="test", task="test")
        trace.add_tool_call(tool_name="send_email", latency_ms=100)
        d = trace.to_dict()
        assert d["tool_calls"] == 1
        assert d["events"][0]["event_type"] == "tool_call"
        assert d["events"][0]["tool_name"] == "send_email"

    def test_tool_call_truncates_previews(self):
        trace = Trace(agent_name="test", task="test")
        long_input = "x" * 500
        event = trace.add_tool_call(
            tool_name="test",
            latency_ms=0,
            tool_input=long_input,
        )
        assert len(event.tool_input_preview) == 200

    def test_tool_call_error_status(self):
        trace = Trace(agent_name="test", task="test")
        event = trace.add_tool_call(
            tool_name="broken_tool",
            latency_ms=10,
            tool_output="Connection refused",
            tool_status="error",
        )
        assert event.tool_status == "error"

    def test_mixed_events(self):
        trace = Trace(agent_name="test", task="test")
        trace.add_llm_call(
            model="gpt-4o", provider="openai",
            input_tokens=10, output_tokens=5,
            cost=0.01, latency_ms=100,
        )
        trace.add_tool_call(tool_name="search", latency_ms=50)
        trace.add_tool_call(tool_name="write_file", latency_ms=30)
        d = trace.to_dict()
        assert d["llm_calls"] == 1
        assert d["tool_calls"] == 2


class TestTrackToolDecorator:
    def test_basic_tracking(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            @track_tool(name="my_tool")
            def my_tool(x: int) -> int:
                return x * 2

            result = my_tool(5)
            assert result == 10
            assert len(trace.events) == 1
            assert trace.events[0].event_type == "tool_call"
            assert trace.events[0].tool_name == "my_tool"
            assert trace.events[0].tool_status == "success"
        finally:
            set_active_trace(None)

    def test_tracks_without_active_trace(self):
        """Should not crash when no trace is active."""
        set_active_trace(None)

        @track_tool(name="standalone")
        def standalone():
            return "ok"

        assert standalone() == "ok"

    def test_tracks_error(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            @track_tool(name="failing_tool")
            def failing():
                raise ValueError("boom")

            with pytest.raises(ValueError, match="boom"):
                failing()

            assert len(trace.events) == 1
            assert trace.events[0].tool_status == "error"
            assert "boom" in trace.events[0].tool_output_preview
        finally:
            set_active_trace(None)

    def test_server_name(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            @track_tool(name="query", server="postgres-mcp")
            def query(sql: str) -> str:
                return "result"

            query("SELECT 1")
            assert trace.events[0].tool_server == "postgres-mcp"
            assert trace.events[0].metadata["server"] == "postgres-mcp"
        finally:
            set_active_trace(None)

    def test_auto_name_from_function(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            @track_tool()
            def my_custom_function():
                return True

            my_custom_function()
            assert trace.events[0].tool_name == "my_custom_function"
        finally:
            set_active_trace(None)

    def test_input_output_previews(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            @track_tool(name="search")
            def search(query: str) -> dict:
                return {"results": [1, 2, 3]}

            search("hello world")
            event = trace.events[0]
            assert "hello world" in event.tool_input_preview
            assert "results" in event.tool_output_preview
        finally:
            set_active_trace(None)


class TestToolApproval:
    def test_approved_executes(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            @tool_approval(name="safe_tool", handler=auto_approve_handler)
            def safe_tool():
                return "done"

            result = safe_tool()
            assert result == "done"

            # Should have approval + tool_call events
            types = [e.event_type for e in trace.events]
            assert "approval_gate" in types
            assert "tool_call" in types
        finally:
            set_active_trace(None)

    def test_denied_raises(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            @tool_approval(name="dangerous_tool", handler=auto_deny_handler)
            def dangerous():
                return "should not run"

            with pytest.raises(ApprovalDenied):
                dangerous()

            # Should have denial events
            tool_events = [e for e in trace.events if e.event_type == "tool_call"]
            assert any(e.tool_status == "denied" for e in tool_events)
        finally:
            set_active_trace(None)


class TestRecordToolCall:
    def test_manual_recording(self):
        trace = Trace(agent_name="test", task="test")
        set_active_trace(trace)

        try:
            record_tool_call(
                tool_name="external_api",
                tool_input={"endpoint": "/users"},
                tool_output={"users": [1, 2, 3]},
                latency_ms=250.5,
                server="rest-api",
            )

            assert len(trace.events) == 1
            event = trace.events[0]
            assert event.tool_name == "external_api"
            assert event.latency_ms == 250.5
            assert event.tool_server == "rest-api"
        finally:
            set_active_trace(None)

    def test_no_crash_without_trace(self):
        set_active_trace(None)
        # Should not raise
        record_tool_call(tool_name="test", tool_input="in", tool_output="out")


class TestToolsInsideAgent:
    def test_tools_tracked_in_agent_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            @track_tool(name="helper_tool")
            def helper(x: int) -> int:
                return x + 1

            @agent(auto_patch=False, trace_dir=tmpdir)
            def my_agent(task: str):
                result = helper(5)
                return f"got {result}"

            output = my_agent.run("test")
            assert output == "got 6"

            traces = my_agent.trace_store.list_traces()
            assert len(traces) == 1
            events = traces[0]["events"]
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(tool_events) == 1
            assert tool_events[0]["tool_name"] == "helper_tool"
            assert traces[0]["tool_calls"] == 1
