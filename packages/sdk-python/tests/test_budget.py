import pytest
from wickd.budget import Budget, BudgetTracker, BudgetExceeded


class TestBudget:
    def test_valid_budget(self):
        b = Budget(per_run=2.0, daily=20.0, monthly=100.0)
        assert b.per_run == 2.0
        assert b.daily == 20.0
        assert b.monthly == 100.0

    def test_no_caps(self):
        b = Budget()
        assert b.per_run is None
        assert b.daily is None
        assert b.monthly is None

    def test_negative_per_run_raises(self):
        with pytest.raises(ValueError, match="per_run"):
            Budget(per_run=-1.0)

    def test_zero_daily_raises(self):
        with pytest.raises(ValueError, match="daily"):
            Budget(daily=0)

    def test_negative_monthly_raises(self):
        with pytest.raises(ValueError, match="monthly"):
            Budget(monthly=-5.0)


class TestBudgetTracker:
    def test_record_cost(self):
        tracker = BudgetTracker(Budget(per_run=10.0))
        tracker.record_cost(1.50, "gpt-4o", 100, 50)
        assert tracker.run_spend == 1.50
        assert tracker.call_count == 1

    def test_remaining(self):
        tracker = BudgetTracker(Budget(per_run=5.0))
        tracker.record_cost(2.0, "gpt-4o", 100, 50)
        assert tracker.remaining() == 3.0

    def test_remaining_no_caps(self):
        tracker = BudgetTracker(Budget())
        tracker.record_cost(100.0, "gpt-4o", 100, 50)
        assert tracker.remaining() is None

    def test_remaining_picks_smallest(self):
        tracker = BudgetTracker(Budget(per_run=10.0, daily=5.0))
        tracker.record_cost(3.0, "gpt-4o", 100, 50)
        assert tracker.remaining() == 2.0

    def test_exceeded_per_run(self):
        tracker = BudgetTracker(Budget(per_run=1.0))
        with pytest.raises(BudgetExceeded) as exc_info:
            tracker.record_cost(1.5, "gpt-4o", 100, 50)
        assert exc_info.value.trigger == "per_run"
        assert exc_info.value.spent == 1.5

    def test_exceeded_daily(self):
        tracker = BudgetTracker(Budget(daily=2.0))
        tracker.record_cost(1.0, "gpt-4o", 100, 50)
        with pytest.raises(BudgetExceeded) as exc_info:
            tracker.record_cost(1.5, "gpt-4o", 100, 50)
        assert exc_info.value.trigger == "daily"

    def test_exceeded_monthly(self):
        tracker = BudgetTracker(Budget(monthly=0.50))
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.60, "gpt-4o", 100, 50)

    def test_pre_call_check_after_kill(self):
        tracker = BudgetTracker(Budget(per_run=1.0))
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(2.0, "gpt-4o", 100, 50)
        assert tracker.is_killed
        with pytest.raises(BudgetExceeded):
            tracker.pre_call_check()

    def test_reset_run(self):
        tracker = BudgetTracker(Budget(per_run=1.0, daily=10.0))
        tracker.record_cost(0.5, "gpt-4o", 100, 50)
        tracker.reset_run()
        assert tracker.run_spend == 0.0
        assert tracker.call_count == 0
        assert not tracker.is_killed
        assert tracker.daily_spend == 0.5  # daily persists

    def test_on_kill_callback(self):
        killed = {}
        budget = Budget(per_run=0.10, on_kill=lambda s: killed.update(s))
        tracker = BudgetTracker(budget)
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.20, "gpt-4o", 100, 50)
        assert killed["killed"] is True
        assert killed["run_spend"] >= 0.20

    def test_summary(self):
        tracker = BudgetTracker(Budget(per_run=5.0, daily=20.0))
        tracker.record_cost(1.0, "gpt-4o", 100, 50)
        s = tracker.summary()
        assert s["run_spend"] == 1.0
        assert s["daily_spend"] == 1.0
        assert s["call_count"] == 1
        assert s["killed"] is False
        assert s["caps"]["per_run"] == 5.0
        assert s["caps"]["daily"] == 20.0
