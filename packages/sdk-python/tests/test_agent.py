import tempfile
import pytest
from wickd.agent import agent, WickdAgent
from wickd.budget import Budget, BudgetExceeded


class TestWickdAgent:
    def test_basic_agent(self):
        @agent(auto_patch=False)
        def my_agent(task: str):
            return f"done: {task}"

        assert isinstance(my_agent, WickdAgent)
        assert my_agent.run("test") == "done: test"

    def test_agent_without_args(self):
        @agent
        def my_agent(task: str):
            return f"done: {task}"
        assert my_agent.run("test") == "done: test"

    def test_agent_with_budget(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(budget=Budget(per_run=5.0), auto_patch=False, trace_dir=tmpdir)
            def my_agent(task: str):
                return "ok"
            assert my_agent.run("test") == "ok"

    def test_callable(self):
        @agent(auto_patch=False)
        def my_agent(task: str):
            return task.upper()
        assert my_agent("hello") == "HELLO"

    def test_creates_trace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(auto_patch=False, trace_dir=tmpdir)
            def my_agent(task: str):
                return "ok"

            my_agent.run("test task")
            traces = my_agent.trace_store.list_traces()
            assert len(traces) == 1
            assert traces[0]["agent_name"] == "my_agent"
            assert traces[0]["status"] == "completed"

    def test_error_traced(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(auto_patch=False, trace_dir=tmpdir)
            def failing_agent(task: str):
                raise ValueError("something broke")

            with pytest.raises(ValueError, match="something broke"):
                failing_agent.run("test")

            traces = failing_agent.trace_store.list_traces()
            assert len(traces) == 1
            assert traces[0]["status"] == "error"

    def test_name_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(name="custom_name", auto_patch=False, trace_dir=tmpdir)
            def my_agent(task: str):
                return "ok"

            my_agent.run("test")
            assert my_agent.trace_store.list_traces()[0]["agent_name"] == "custom_name"

    def test_on_run_complete(self):
        events = []

        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(auto_patch=False, on_run_complete=events.append, trace_dir=tmpdir)
            def my_agent(task: str):
                return "ok"

            my_agent.run("test")

        assert len(events) == 1
        assert events[0]["type"] == "run_complete"
        assert events[0]["status"] == "completed"
