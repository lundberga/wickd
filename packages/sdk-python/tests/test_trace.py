import json
import tempfile
import pytest
from wickd.trace import Trace, TraceEvent, TraceStore


class TestTraceEvent:
    def test_to_dict_strips_none(self):
        event = TraceEvent(event_type="llm_call", model="gpt-4o", cost=0.01)
        d = event.to_dict()
        assert "model" in d
        assert "approval_name" not in d
        assert "error_message" not in d

    def test_defaults(self):
        event = TraceEvent(event_type="test")
        assert event.event_type == "test"
        assert event.timestamp > 0
        assert len(event.event_id) == 8


class TestTrace:
    def test_new_trace(self):
        t = Trace(agent_name="test", task="do stuff")
        assert t.status == "running"
        assert t.total_cost == 0.0
        assert len(t.events) == 0

    def test_add_llm_call(self):
        t = Trace(agent_name="test")
        t.add_llm_call(model="gpt-4o", provider="openai",
                       input_tokens=500, output_tokens=200, cost=0.0045, latency_ms=150.0)
        assert len(t.events) == 1
        assert t.total_cost == 0.0045
        assert t.total_input_tokens == 500
        assert t.total_output_tokens == 200

    def test_cumulative_cost(self):
        t = Trace(agent_name="test")
        t.add_llm_call("gpt-4o", "openai", 100, 50, 0.01, 100.0)
        t.add_llm_call("gpt-4o", "openai", 100, 50, 0.02, 100.0)
        assert t.total_cost == pytest.approx(0.03)
        assert t.events[1].spend_at_event == pytest.approx(0.03)

    def test_add_approval(self):
        t = Trace(agent_name="test")
        t.add_approval("db_write", "approved")
        assert len(t.events) == 1
        assert t.events[0].approval_name == "db_write"
        assert t.events[0].approval_status == "approved"

    def test_add_budget_kill(self):
        t = Trace(agent_name="test")
        t.add_budget_kill("per_run", 2.50)
        assert t.status == "budget_killed"
        assert t.events[0].budget_trigger == "per_run"

    def test_complete(self):
        t = Trace(agent_name="test")
        t.complete()
        assert t.status == "completed"
        assert t.ended_at is not None
        assert t.duration_ms() >= 0

    def test_complete_preserves_killed_status(self):
        t = Trace(agent_name="test")
        t.add_budget_kill("per_run", 1.0)
        t.complete()
        assert t.status == "budget_killed"

    def test_to_dict(self):
        t = Trace(agent_name="test", task="hello")
        t.add_llm_call("gpt-4o", "openai", 100, 50, 0.01, 100.0)
        t.complete(budget_summary={"run_spend": 0.01})
        d = t.to_dict()
        assert d["agent_name"] == "test"
        assert d["task"] == "hello"
        assert d["status"] == "completed"
        assert d["llm_calls"] == 1
        assert len(d["events"]) == 1
        assert d["budget_summary"]["run_spend"] == 0.01

    def test_to_json_roundtrips(self):
        t = Trace(agent_name="test")
        t.complete()
        assert json.loads(t.to_json())["agent_name"] == "test"


class TestTraceStore:
    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TraceStore(tmpdir)
            t = Trace(agent_name="test_agent", task="test task")
            t.complete()
            assert store.save(t).exists()
            traces = store.list_traces()
            assert len(traces) == 1
            assert traces[0]["agent_name"] == "test_agent"

    def test_get_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TraceStore(tmpdir)
            t = Trace(agent_name="test_agent")
            t.complete()
            store.save(t)
            loaded = store.get_trace(t.trace_id[:8])
            assert loaded is not None
            assert loaded["trace_id"] == t.trace_id

    def test_get_trace_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert TraceStore(tmpdir).get_trace("nonexistent") is None

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert TraceStore(tmpdir).list_traces() == []

    def test_list_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = TraceStore(tmpdir)
            for i in range(5):
                t = Trace(agent_name=f"agent_{i}")
                t.complete()
                store.save(t)
            assert len(store.list_traces(limit=3)) == 3
