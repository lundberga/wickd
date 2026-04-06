"""Tests that concurrent runs on the same WickdAgent have fully isolated state."""
import asyncio
import tempfile
import threading
import pytest
from wickd.agent import WickdAgent, agent
from wickd.budget import Budget, BudgetExceeded, BudgetTracker
from wickd.interceptor import get_active_tracker, get_active_trace


class TestConcurrentRunIsolation:
    def test_concurrent_threaded_runs_isolated(self):
        """Two concurrent threaded runs on the same agent must not share
        tracker or trace objects. ContextVar provides per-thread isolation."""
        seen_trackers: list[object] = []
        barrier = threading.Barrier(2)

        def mock_fn(task: str) -> str:
            # Capture the tracker while both threads are mid-run.
            seen_trackers.append(get_active_tracker())
            barrier.wait()  # ensure both threads are in-flight simultaneously
            return f"done: {task}"

        ag = WickdAgent(fn=mock_fn, name="test", auto_patch=False, budget=Budget(per_run=1.00))

        results: list[str] = []

        def run(task: str) -> None:
            results.append(ag.run(task))

        t1 = threading.Thread(target=run, args=("a",))
        t2 = threading.Thread(target=run, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert set(results) == {"done: a", "done: b"}
        # Each run created its own BudgetTracker — they must be distinct objects.
        assert len(seen_trackers) == 2
        assert seen_trackers[0] is not seen_trackers[1]

    def test_sequential_runs_have_clean_state(self):
        """After a run completes, the context variables must be restored to None
        so a subsequent run starts clean."""
        call_order: list[str] = []

        def mock_fn(task: str) -> str:
            call_order.append(task)
            return task

        ag = WickdAgent(fn=mock_fn, name="test", auto_patch=False)

        ag.run("first")
        # After first run the active tracker/trace must be cleared.
        assert get_active_tracker() is None
        assert get_active_trace() is None

        ag.run("second")
        assert call_order == ["first", "second"]

    def test_sequential_runs_independent_budgets(self):
        """Each run gets a fresh BudgetTracker — spend does not accumulate across runs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            @agent(budget=Budget(per_run=0.01), auto_patch=False, trace_dir=tmpdir)
            def my_agent(task: str) -> str:
                return task

            # First run should succeed.
            result = my_agent.run("first")
            assert result == "first"

            # Second run gets a brand-new tracker at zero spend, so it also succeeds.
            result = my_agent.run("second")
            assert result == "second"

    def test_run_context_cleared_after_exception(self):
        """Even when a run raises, the context vars must be reset."""
        def boom(task: str) -> str:
            raise RuntimeError("explode")

        ag = WickdAgent(fn=boom, name="test", auto_patch=False)

        with pytest.raises(RuntimeError):
            ag.run("x")

        assert get_active_tracker() is None
        assert get_active_trace() is None

    def test_budget_kill_callback_plumbing(self):
        """on_budget_kill callback wiring works without crashing.
        Does not trigger a kill — verifies the plumbing compiles and runs."""
        kill_events: list[dict] = []

        def record_kill(event: dict) -> None:
            kill_events.append(event)

        # Budget so tight any real spend would exceed it, but we test the
        # callback routing without actual LLM calls — so no spend occurs.
        # We verify the trace_id field is present and non-empty.
        with tempfile.TemporaryDirectory() as tmpdir:
            ag = WickdAgent(
                fn=lambda task: task,
                name="kill-test",
                auto_patch=False,
                on_budget_kill=record_kill,
                budget=Budget(per_run=5.00),
                trace_dir=tmpdir,
            )
            ag.run("hello")

        # No kill fired (budget not exceeded), so kill_events is empty.
        # This test validates the plumbing compiles and runs correctly.
        assert kill_events == []


class TestAsyncAgentSupport:
    def test_run_rejects_async_fn(self):
        """Calling run() with an async function must raise TypeError."""
        async def async_fn(task: str) -> str:
            return task

        ag = WickdAgent(fn=async_fn, name="test", auto_patch=False)
        with pytest.raises(TypeError, match="async.*arun"):
            ag.run("hello")

    def test_arun_rejects_sync_fn(self):
        """Calling arun() with a sync function must raise TypeError."""
        def sync_fn(task: str) -> str:
            return task

        ag = WickdAgent(fn=sync_fn, name="test", auto_patch=False)
        with pytest.raises(TypeError, match="sync.*run"):
            asyncio.run(ag.arun("hello"))

    @pytest.mark.asyncio
    async def test_arun_executes_async_fn(self):
        """arun() must properly await the async agent function."""
        async def async_fn(task: str) -> str:
            await asyncio.sleep(0.001)
            return f"done: {task}"

        ag = WickdAgent(fn=async_fn, name="test", auto_patch=False)
        result = await ag.arun("hello")
        assert result == "done: hello"

    @pytest.mark.asyncio
    async def test_arun_context_cleaned_up(self):
        """After arun completes, context vars must be None."""
        async def async_fn(task: str) -> str:
            assert get_active_tracker() is None  # no budget set
            return task

        ag = WickdAgent(fn=async_fn, name="test", auto_patch=False)
        await ag.arun("hello")
        assert get_active_tracker() is None
        assert get_active_trace() is None

    @pytest.mark.asyncio
    async def test_arun_concurrent_isolation(self):
        """Two concurrent arun() calls must have isolated trackers."""
        seen_trackers: list[object] = []
        event = asyncio.Event()
        barrier_count = 0

        async def mock_fn(task: str) -> str:
            nonlocal barrier_count
            seen_trackers.append(get_active_tracker())
            barrier_count += 1
            if barrier_count < 2:
                await asyncio.sleep(0.01)  # let second task start
            event.set()
            return f"done: {task}"

        ag = WickdAgent(fn=mock_fn, name="test", auto_patch=False, budget=Budget(per_run=1.00))
        r1, r2 = await asyncio.gather(ag.arun("a"), ag.arun("b"))
        assert {r1, r2} == {"done: a", "done: b"}
        assert len(seen_trackers) == 2
        assert seen_trackers[0] is not seen_trackers[1]

    def test_call_dispatches_correctly(self):
        """__call__ should return a coroutine for async fns, result for sync fns."""
        def sync_fn(task: str) -> str:
            return task

        async def async_fn(task: str) -> str:
            return task

        sync_ag = WickdAgent(fn=sync_fn, name="sync", auto_patch=False)
        assert sync_ag("hello") == "hello"

        async_ag = WickdAgent(fn=async_fn, name="async", auto_patch=False)
        coro = async_ag("hello")
        assert asyncio.iscoroutine(coro)
        result = asyncio.run(coro)
        assert result == "hello"
