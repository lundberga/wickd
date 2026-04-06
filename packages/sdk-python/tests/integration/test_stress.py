"""Stress tests for concurrent agent runs with real API calls.

Verifies budget isolation holds under load with real LLM responses.
Uses sequential calls with staggering to respect rate limits on free-tier accounts.
Costs ~$0.01 per run.
"""

import asyncio
import threading
import time
import pytest
from tests.integration.conftest import skip_no_openai

import wickd
from wickd.budget import Budget, BudgetExceeded
from wickd.interceptor import get_active_tracker


@skip_no_openai
class TestConcurrentStress:
    def test_threaded_runs_isolated(self):
        """Concurrent threaded runs must all have independent budgets.

        Uses batches of 3 to stay within free-tier RPM limits.
        """
        import openai

        results: list[dict] = []
        errors: list[str] = []
        n_runs = 6  # 2 batches of 3

        @wickd.agent(budget=Budget(per_run=0.50), auto_patch=True)
        def agent_fn(task_id: int) -> int:
            client = openai.OpenAI()
            client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": f"Say {task_id}"}],
                max_tokens=5,
            )
            tracker = get_active_tracker()
            results.append({
                "task_id": task_id,
                "spend": tracker.run_spend if tracker else -1,
                "tracker_id": id(tracker),
            })
            return task_id

        def run_one(task_id: int):
            try:
                agent_fn.run(task_id)
            except Exception as e:
                errors.append(f"task {task_id}: {e}")

        # Run in batches of 3 to respect rate limits
        batch_size = 3
        for batch_start in range(0, n_runs, batch_size):
            batch_end = min(batch_start + batch_size, n_runs)
            threads = [
                threading.Thread(target=run_one, args=(i,))
                for i in range(batch_start, batch_end)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            if batch_end < n_runs:
                time.sleep(21)  # wait for rate limit window to reset

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == n_runs

        # Every tracker must be a unique object (isolation proof)
        tracker_ids = [r["tracker_id"] for r in results]
        assert len(set(tracker_ids)) == n_runs, "Some runs shared a tracker!"

        # Every run should have recorded some spend
        for r in results:
            assert r["spend"] > 0, f"Task {r['task_id']} had zero spend"

    @pytest.mark.asyncio
    async def test_async_runs_isolated(self):
        """Concurrent async runs must have independent budgets.

        Runs 3 at a time to respect rate limits.
        """
        import openai

        results: list[dict] = []

        async def agent_fn(task_id: int) -> int:
            client = openai.AsyncOpenAI()
            await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": f"Say {task_id}"}],
                max_tokens=5,
            )
            tracker = get_active_tracker()
            results.append({
                "task_id": task_id,
                "spend": tracker.run_spend if tracker else -1,
                "tracker_id": id(tracker),
            })
            return task_id

        ag = wickd.agent(agent_fn, budget=Budget(per_run=0.50))

        # Batch async runs to respect rate limits
        for batch_start in range(0, 6, 3):
            tasks = [ag.arun(i) for i in range(batch_start, batch_start + 3)]
            await asyncio.gather(*tasks)
            if batch_start + 3 < 6:
                await asyncio.sleep(21)

        assert len(results) == 6

        tracker_ids = [r["tracker_id"] for r in results]
        assert len(set(tracker_ids)) == 6, "Some async runs shared a tracker!"

        for r in results:
            assert r["spend"] > 0, f"Task {r['task_id']} had zero spend"
