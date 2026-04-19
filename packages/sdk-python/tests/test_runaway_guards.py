"""Tests for non-cost runaway kill switches: max_llm_calls, max_tool_calls,
max_duration_seconds."""

import asyncio
import tempfile
import time

import pytest

from wickd.agent import agent
from wickd.budget import Budget, BudgetTracker, BudgetExceeded
from wickd.trace import Trace
from wickd.interceptor import set_active_trace, set_active_tracker
from wickd.tools import track_tool, record_tool_call


# ── Budget validation ──────────────────────────────────────────────────────

class TestBudgetValidation:
    def test_max_llm_calls_rejects_zero(self):
        with pytest.raises(ValueError, match="positive int"):
            Budget(max_llm_calls=0)

    def test_max_llm_calls_rejects_negative(self):
        with pytest.raises(ValueError, match="positive int"):
            Budget(max_llm_calls=-1)

    def test_max_llm_calls_rejects_float(self):
        with pytest.raises(ValueError, match="positive int"):
            Budget(max_llm_calls=5.0)

    def test_max_tool_calls_rejects_zero(self):
        with pytest.raises(ValueError, match="positive int"):
            Budget(max_tool_calls=0)

    def test_max_duration_rejects_zero(self):
        with pytest.raises(ValueError, match="max_duration_seconds must be positive"):
            Budget(max_duration_seconds=0)

    def test_all_guards_optional(self):
        b = Budget()
        assert b.max_llm_calls is None
        assert b.max_tool_calls is None
        assert b.max_duration_seconds is None


# ── BudgetTracker enforcement ──────────────────────────────────────────────

class TestMaxLlmCalls:
    def test_kills_at_cap(self):
        t = BudgetTracker(Budget(max_llm_calls=3))
        t.record_cost(0.001)
        t.record_cost(0.001)
        with pytest.raises(BudgetExceeded) as exc_info:
            t.record_cost(0.001)
        assert exc_info.value.trigger == "max_llm_calls"
        assert exc_info.value.measured == 3
        assert "calls" in str(exc_info.value)

    def test_allows_under_cap(self):
        t = BudgetTracker(Budget(max_llm_calls=5))
        for _ in range(4):
            t.record_cost(0.01)
        assert t.call_count == 4
        assert not t.is_killed


class TestMaxToolCalls:
    def test_kills_at_cap(self):
        t = BudgetTracker(Budget(max_tool_calls=2))
        t.record_tool_call()
        with pytest.raises(BudgetExceeded) as exc_info:
            t.record_tool_call()
        assert exc_info.value.trigger == "max_tool_calls"
        assert exc_info.value.measured == 2

    def test_independent_of_llm_count(self):
        t = BudgetTracker(Budget(max_tool_calls=1, max_llm_calls=10))
        # LLM calls don't count toward tool cap.
        for _ in range(5):
            t.record_cost(0.001)
        with pytest.raises(BudgetExceeded, match="max_tool_calls"):
            t.record_tool_call()


class TestMaxDuration:
    def test_kills_after_elapsed(self):
        t = BudgetTracker(Budget(max_duration_seconds=0.05))
        # No calls yet — tracker doesn't check duration until something happens.
        time.sleep(0.06)
        with pytest.raises(BudgetExceeded) as exc_info:
            t.record_cost(0.001)
        assert exc_info.value.trigger == "max_duration"
        assert exc_info.value.measured >= 0.05

    def test_tool_call_triggers_duration_check(self):
        t = BudgetTracker(Budget(max_duration_seconds=0.05))
        time.sleep(0.06)
        with pytest.raises(BudgetExceeded, match="max_duration"):
            t.record_tool_call()

    def test_elapsed_property(self):
        t = BudgetTracker(Budget())
        time.sleep(0.02)
        assert t.elapsed_seconds >= 0.02


class TestCombinedCaps:
    def test_cost_cap_still_works_alongside(self):
        t = BudgetTracker(Budget(per_run=0.02, max_llm_calls=100))
        with pytest.raises(BudgetExceeded) as exc_info:
            t.record_cost(0.03)
        assert exc_info.value.trigger == "per_run"

    def test_whichever_trips_first_wins(self):
        # Cost cap tight, call cap loose — cost should win.
        t = BudgetTracker(Budget(per_run=0.02, max_llm_calls=10))
        t.record_cost(0.01)
        with pytest.raises(BudgetExceeded) as exc_info:
            t.record_cost(0.02)
        assert exc_info.value.trigger == "per_run"


# ── Summary and reset ──────────────────────────────────────────────────────

class TestSummaryAndReset:
    def test_summary_exposes_new_counters(self):
        t = BudgetTracker(Budget(max_llm_calls=5, max_tool_calls=10, max_duration_seconds=60))
        t.record_cost(0.01)
        t.record_tool_call()
        s = t.summary()
        assert s["call_count"] == 1
        assert s["tool_call_count"] == 1
        assert s["elapsed_seconds"] >= 0
        caps = s["caps"]
        assert caps["max_llm_calls"] == 5
        assert caps["max_tool_calls"] == 10
        assert caps["max_duration_seconds"] == 60

    def test_reset_clears_tool_count(self):
        t = BudgetTracker(Budget(max_tool_calls=3))
        t.record_tool_call()
        t.record_tool_call()
        t.reset_run()
        assert t.tool_call_count == 0
        # Should no longer be near the cap.
        t.record_tool_call()
        assert t.tool_call_count == 1


# ── Wiring: track_tool decorator increments tracker ────────────────────────

class TestTrackToolIntegration:
    def test_decorator_increments_tracker(self):
        trace = Trace(agent_name="t", task="t")
        tracker = BudgetTracker(Budget(max_tool_calls=2))
        set_active_trace(trace)
        set_active_tracker(tracker)

        try:
            @track_tool(name="search")
            def search(q):
                return f"found {q}"

            search("a")
            assert tracker.tool_call_count == 1
            with pytest.raises(BudgetExceeded, match="max_tool_calls"):
                search("b")
        finally:
            set_active_trace(None)
            set_active_tracker(None)

    def test_denied_does_not_count(self):
        from wickd.tools import tool_approval
        from wickd.approvals import auto_deny_handler, ApprovalDenied

        trace = Trace(agent_name="t", task="t")
        tracker = BudgetTracker(Budget(max_tool_calls=1))
        set_active_trace(trace)
        set_active_tracker(tracker)

        try:
            @tool_approval(name="dangerous", handler=auto_deny_handler)
            def dangerous():
                return "ok"

            # Denial should NOT count toward tool cap.
            with pytest.raises(ApprovalDenied):
                dangerous()
            assert tracker.tool_call_count == 0
        finally:
            set_active_trace(None)
            set_active_tracker(None)


# ── End-to-end: agent-level runaway guards ─────────────────────────────────

class TestAgentLevelGuards:
    def test_agent_kill_on_max_llm_calls(self):
        """A tool-heavy agent makes too many tool calls and gets killed."""
        from wickd.tools import track_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            @track_tool(name="step")
            def step(x):
                return x + 1

            @agent(
                budget=Budget(max_tool_calls=5),
                trace_dir=tmpdir,
                on_patch_failure="allow",
            )
            def loopy():
                for i in range(100):
                    step(i)
                return "done"

            with pytest.raises(BudgetExceeded, match="max_tool_calls"):
                loopy.run()

            # Trace should record the budget kill.
            traces = loopy.trace_store.list_traces()
            latest = traces[0]
            assert latest["status"] == "budget_killed"
            kill_events = [e for e in latest["events"] if e["event_type"] == "budget_kill"]
            assert len(kill_events) == 1
            assert kill_events[0]["budget_trigger"] == "max_tool_calls"

    def test_agent_kill_on_duration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            @track_tool(name="slow_step")
            def slow_step():
                time.sleep(0.02)
                return "ok"

            @agent(
                budget=Budget(max_duration_seconds=0.05),
                trace_dir=tmpdir,
                on_patch_failure="allow",
            )
            def long_running():
                for _ in range(20):
                    slow_step()
                return "done"

            with pytest.raises(BudgetExceeded, match="max_duration"):
                long_running.run()


# ── End-to-end: MCP-aware guards ───────────────────────────────────────────

class TestMcpGuards:
    def test_mcp_tool_calls_count_toward_guard(self, tmp_path):
        """An agent using MCP that loops hits max_tool_calls."""
        import sys
        import types

        # Install a minimal fake mcp module.
        class Result:
            def __init__(self, text):
                self.content = [types.SimpleNamespace(text=text)]
                self.isError = False

        class FakeSession:
            async def call_tool(self, name, arguments=None):
                return Result(f"ok:{name}")

        module = types.ModuleType("mcp")
        module.ClientSession = FakeSession
        module.__version__ = "0.0.0-fake"
        sys.modules.pop("mcp", None)
        sys.modules["mcp"] = module
        sys.modules.pop("wickd.mcp_patch", None)

        try:
            from wickd.mcp_patch import patch_mcp, unpatch_mcp
            patch_mcp()

            @agent(
                budget=Budget(max_tool_calls=3),
                trace_dir=str(tmp_path),
                on_patch_failure="allow",
            )
            async def mcp_agent():
                session = FakeSession()
                for i in range(10):
                    await session.call_tool(f"step-{i}")
                return "done"

            with pytest.raises(BudgetExceeded, match="max_tool_calls"):
                asyncio.run(mcp_agent.arun())
        finally:
            try:
                unpatch_mcp()
            except Exception:
                pass
            sys.modules.pop("mcp", None)
            sys.modules.pop("wickd.mcp_patch", None)
