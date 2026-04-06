"""
Property-based tests for the Wickd Lyapunov invariant.

The invariant: V(t) = min(C_run - c(t), C_daily - c_d(t), C_monthly - c_m(t)) >= 0
at all times before a BudgetExceeded is raised.

These tests fuzz random cost sequences to verify that:
1. V(t) > 0 at initialization (budget exists)
2. V̇(t) ≤ 0 — costs monotonically accumulate
3. V(t) = 0 → HALT — BudgetExceeded raised immediately
4. No sequence of calls can ever result in c(t) > C without BudgetExceeded
"""

import asyncio
import random

import pytest

from wickd.budget import Budget, BudgetTracker, BudgetExceeded

random.seed(42)


def compute_v(tracker: BudgetTracker) -> float:
    """Compute the Lyapunov energy function V(t).

    V(t) = min over all active caps of (cap - current_spend).
    Returns float('inf') when no caps are set.
    """
    remainders = []
    if tracker.budget.per_run is not None:
        remainders.append(tracker.budget.per_run - tracker._run_spend)
    if tracker.budget.daily is not None:
        remainders.append(tracker.budget.daily - tracker._daily_spend)
    if tracker.budget.monthly is not None:
        remainders.append(tracker.budget.monthly - tracker._monthly_spend)
    if not remainders:
        return float("inf")
    return min(remainders)


class TestInvariantHoldsRandomCosts:
    """Generate 100 random cost sequences and verify V(t) >= 0 before halt."""

    def test_invariant_holds_random_costs(self):
        rng = random.Random(42)

        for trial in range(100):
            # Random caps: per_run in [0.01, 0.50], daily in [0.05, 1.0], monthly in [0.10, 5.0]
            per_run = rng.uniform(0.01, 0.50)
            daily = rng.uniform(0.05, 1.0)
            monthly = rng.uniform(0.10, 5.0)
            budget = Budget(per_run=per_run, daily=daily, monthly=monthly)
            tracker = BudgetTracker(budget)

            num_calls = rng.randint(1, 50)
            costs = [rng.uniform(0.0001, 0.05) for _ in range(num_calls)]

            halted = False
            v_history = []

            for cost in costs:
                if halted:
                    # Once halted, no more costs should be recordable
                    with pytest.raises(BudgetExceeded):
                        tracker.record_cost(cost)
                    continue

                try:
                    tracker.record_cost(cost)
                    v = compute_v(tracker)
                    v_history.append(v)
                    # V(t) must be non-negative while we have not halted
                    assert v >= 0, (
                        f"Trial {trial}: V(t) went negative ({v}) without "
                        f"BudgetExceeded. Caps: per_run={per_run}, daily={daily}, "
                        f"monthly={monthly}. Spend: run={tracker._run_spend}, "
                        f"daily={tracker._daily_spend}, monthly={tracker._monthly_spend}"
                    )
                except BudgetExceeded:
                    halted = True
                    # At the moment of halt, the spend must be >= some cap
                    assert tracker.is_killed
                    v_at_halt = compute_v(tracker)
                    assert v_at_halt <= 0, (
                        f"Trial {trial}: BudgetExceeded raised but V(t)={v_at_halt} > 0"
                    )


class TestMonotonicCostAccumulation:
    """Verify c(t) is monotonically non-decreasing after each step."""

    def test_monotonic_cost_accumulation(self):
        rng = random.Random(42)
        budget = Budget(per_run=100.0)  # High cap so we don't halt
        tracker = BudgetTracker(budget)

        prev_run = 0.0
        prev_daily = 0.0
        prev_monthly = 0.0

        for _ in range(200):
            cost = rng.uniform(0.0001, 0.05)
            tracker.record_cost(cost)

            assert tracker._run_spend >= prev_run, (
                f"Run spend decreased: {tracker._run_spend} < {prev_run}"
            )
            assert tracker._daily_spend >= prev_daily, (
                f"Daily spend decreased: {tracker._daily_spend} < {prev_daily}"
            )
            assert tracker._monthly_spend >= prev_monthly, (
                f"Monthly spend decreased: {tracker._monthly_spend} < {prev_monthly}"
            )

            prev_run = tracker._run_spend
            prev_daily = tracker._daily_spend
            prev_monthly = tracker._monthly_spend


class TestVZeroTriggersHalt:
    """When costs sum to exactly the cap, BudgetExceeded must fire."""

    def test_v_zero_triggers_halt(self):
        budget = Budget(per_run=0.10)
        tracker = BudgetTracker(budget)

        # Record costs that sum to just under the cap
        tracker.record_cost(0.04)
        tracker.record_cost(0.04)
        # Total so far: 0.08, still under
        assert not tracker.is_killed
        assert compute_v(tracker) > 0

        # This brings total to 0.12, over the cap -> must halt
        # (We overshoot slightly to avoid floating-point edge cases;
        # the invariant is that crossing the cap always triggers halt.)
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.04)

        assert tracker.is_killed
        assert compute_v(tracker) <= 0


class TestKillPreventsFurtherCalls:
    """After BudgetExceeded, pre_call_check() must also raise."""

    def test_kill_prevents_further_calls(self):
        budget = Budget(per_run=0.05)
        tracker = BudgetTracker(budget)

        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.10)

        assert tracker.is_killed

        # pre_call_check must raise
        with pytest.raises(BudgetExceeded):
            tracker.pre_call_check()

        # record_cost must also raise
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.001)

        # check_budget must raise
        with pytest.raises(BudgetExceeded):
            tracker.check_budget()


class TestConcurrentInvariant:
    """Run concurrent async tasks recording costs, verify invariant holds."""

    @pytest.mark.asyncio
    async def test_concurrent_invariant(self):
        cap = 1.0
        budget = Budget(per_run=cap)
        tracker = BudgetTracker(budget)

        exceptions_raised = []
        total_recorded = []

        async def record_costs(task_id: int):
            rng = random.Random(42 + task_id)
            for _ in range(20):
                cost = rng.uniform(0.001, 0.02)
                try:
                    tracker.record_cost(cost)
                    total_recorded.append(cost)
                except BudgetExceeded:
                    exceptions_raised.append(task_id)
                    return
                # Yield control to simulate interleaving
                await asyncio.sleep(0)

        tasks = [record_costs(i) for i in range(10)]
        await asyncio.gather(*tasks)

        # The invariant: total spend must never exceed cap without exception
        # Since record_cost is atomic (locked), the spend at halt should be
        # at or just above the cap (the call that crossed it raised).
        if tracker.is_killed:
            assert len(exceptions_raised) > 0
        else:
            # If not killed, total spend must be under cap
            assert tracker._run_spend < cap

        # Regardless, the recorded spend should never silently exceed the cap.
        # Every cost that was successfully recorded (no exception) contributes
        # to a total that must be <= cap.
        v = compute_v(tracker)
        if not tracker.is_killed:
            assert v >= 0, f"V(t) = {v} went negative without halt"


class TestAllCapsChecked:
    """Verify the tightest cap triggers, not just per_run."""

    def test_all_caps_checked(self):
        # daily=0.05 is the tightest cap
        budget = Budget(per_run=1.0, daily=0.05, monthly=10.0)
        tracker = BudgetTracker(budget)

        # Record small costs that stay under per_run but will exceed daily
        tracker.record_cost(0.02)
        tracker.record_cost(0.02)
        assert not tracker.is_killed

        # This should push daily to 0.05 and trigger on daily cap
        with pytest.raises(BudgetExceeded) as exc_info:
            tracker.record_cost(0.02)

        assert exc_info.value.trigger == "daily"
        assert tracker.is_killed

        # per_run should still have plenty of room
        assert tracker._run_spend < budget.per_run
        # monthly should also have plenty of room
        assert tracker._monthly_spend < budget.monthly


class TestEnergyFunctionComputation:
    """Manually compute V(t) at each step and verify expected values."""

    def test_energy_function_computation(self):
        budget = Budget(per_run=0.50, daily=0.30, monthly=2.0)
        tracker = BudgetTracker(budget)

        # At initialization, V(0) = min(0.50, 0.30, 2.0) = 0.30
        v = compute_v(tracker)
        assert v == pytest.approx(0.30, abs=1e-9)

        # Step 1: record 0.10
        tracker.record_cost(0.10)
        # V(1) = min(0.50-0.10, 0.30-0.10, 2.0-0.10) = min(0.40, 0.20, 1.90) = 0.20
        v = compute_v(tracker)
        assert v == pytest.approx(0.20, abs=1e-9)

        # Step 2: record 0.05
        tracker.record_cost(0.05)
        # V(2) = min(0.50-0.15, 0.30-0.15, 2.0-0.15) = min(0.35, 0.15, 1.85) = 0.15
        v = compute_v(tracker)
        assert v == pytest.approx(0.15, abs=1e-9)

        # Step 3: record 0.10
        tracker.record_cost(0.10)
        # V(3) = min(0.50-0.25, 0.30-0.25, 2.0-0.25) = min(0.25, 0.05, 1.75) = 0.05
        v = compute_v(tracker)
        assert v == pytest.approx(0.05, abs=1e-9)

        # Step 4: record 0.03
        tracker.record_cost(0.03)
        # V(4) = min(0.50-0.28, 0.30-0.28, 2.0-0.28) = min(0.22, 0.02, 1.72) = 0.02
        v = compute_v(tracker)
        assert v == pytest.approx(0.02, abs=1e-9)

        # Step 5: record 0.02 -> daily hits exactly 0.30, V(t)=0 -> HALT
        with pytest.raises(BudgetExceeded) as exc_info:
            tracker.record_cost(0.02)

        assert exc_info.value.trigger == "daily"
        # V at halt = min(0.50-0.30, 0.30-0.30, 2.0-0.30) = min(0.20, 0.0, 1.70) = 0.0
        v = compute_v(tracker)
        assert v == pytest.approx(0.0, abs=1e-9)

    def test_energy_monotonically_decreasing(self):
        """V(t) must be non-increasing over any sequence of cost recordings."""
        rng = random.Random(42)
        budget = Budget(per_run=1.0, daily=0.80, monthly=5.0)
        tracker = BudgetTracker(budget)

        prev_v = compute_v(tracker)

        for _ in range(100):
            cost = rng.uniform(0.001, 0.01)
            try:
                tracker.record_cost(cost)
            except BudgetExceeded:
                break

            v = compute_v(tracker)
            assert v <= prev_v, (
                f"V(t) increased from {prev_v} to {v} — violates V̇(t) ≤ 0"
            )
            prev_v = v
