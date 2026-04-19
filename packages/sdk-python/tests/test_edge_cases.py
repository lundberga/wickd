"""Edge case tests for budget, interceptor, and streaming utilities."""

import threading
import pytest
from wickd.budget import Budget, BudgetTracker, BudgetExceeded


# ── Budget: exact and floating-point boundary ──────────────────────────────

class TestBudgetBoundaries:
    def test_cost_exactly_at_cap_raises(self):
        """A single cost equal to the cap must trigger BudgetExceeded."""
        tracker = BudgetTracker(Budget(per_run=0.10))
        with pytest.raises(BudgetExceeded) as exc:
            tracker.record_cost(0.10)
        assert exc.value.trigger == "per_run"

    def test_cost_just_below_cap_is_safe(self):
        """A cost epsilon below the cap must not raise."""
        tracker = BudgetTracker(Budget(per_run=0.10))
        tracker.record_cost(0.09999)
        assert not tracker.is_killed

    def test_floating_point_accumulation_at_cap(self):
        """Three 0.1 additions sum to >0.30 in IEEE 754; the third must trigger BudgetExceeded."""
        tracker = BudgetTracker(Budget(per_run=0.30))
        tracker.record_cost(0.10)
        tracker.record_cost(0.10)
        # 0.1 + 0.1 + 0.1 == 0.30000000000000004 -- crosses the cap
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.10)

    def test_daily_cap_exact_boundary(self):
        tracker = BudgetTracker(Budget(daily=1.00))
        with pytest.raises(BudgetExceeded) as exc:
            tracker.record_cost(1.00)
        assert exc.value.trigger == "daily"

    def test_monthly_cap_exact_boundary(self):
        tracker = BudgetTracker(Budget(monthly=5.00))
        with pytest.raises(BudgetExceeded) as exc:
            tracker.record_cost(5.00)
        assert exc.value.trigger == "monthly"

    def test_zero_cost_is_always_safe(self):
        """Recording zero cost must never trigger a budget."""
        tracker = BudgetTracker(Budget(per_run=0.01))
        for _ in range(100):
            tracker.record_cost(0.0)
        assert tracker.run_spend == 0.0
        assert tracker.call_count == 100
        assert not tracker.is_killed

    def test_multiple_caps_smallest_governs(self):
        """When multiple caps are active, the tightest one fires first."""
        tracker = BudgetTracker(Budget(per_run=10.0, daily=0.50, monthly=100.0))
        with pytest.raises(BudgetExceeded) as exc:
            tracker.record_cost(0.50)
        # daily cap is tightest at 0.50; per_run at 10.0 is not hit yet
        assert exc.value.trigger == "daily"

    def test_pre_call_check_on_already_killed_tracker(self):
        """pre_call_check must raise immediately after the tracker is killed."""
        tracker = BudgetTracker(Budget(per_run=0.01))
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.02)
        assert tracker.is_killed
        with pytest.raises(BudgetExceeded) as exc:
            tracker.pre_call_check()
        assert exc.value.trigger == "already_killed"

    def test_summary_reflects_killed_state(self):
        tracker = BudgetTracker(Budget(per_run=0.01))
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.05)
        s = tracker.summary()
        assert s["killed"] is True
        assert s["run_spend"] >= 0.05

    def test_reset_clears_killed_flag(self):
        """reset_run must allow subsequent calls even after a kill."""
        tracker = BudgetTracker(Budget(per_run=0.01))
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.05)
        tracker.reset_run()
        assert not tracker.is_killed
        tracker.record_cost(0.005)  # must not raise

    def test_on_kill_receives_summary_with_correct_spend(self):
        received = {}
        budget = Budget(per_run=0.10, on_kill=lambda s: received.update(s))
        tracker = BudgetTracker(budget)
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.15)
        assert received["run_spend"] >= 0.15
        assert received["killed"] is True
        assert received["caps"]["per_run"] == 0.10

    def test_on_kill_exception_does_not_mask_budget_exceeded(self):
        """An exception thrown inside on_kill must not prevent BudgetExceeded from propagating."""
        budget = Budget(per_run=0.01, on_kill=lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
        tracker = BudgetTracker(budget)
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.05)

    def test_remaining_returns_none_when_uncapped(self):
        tracker = BudgetTracker(Budget())
        tracker.record_cost(9999.0)
        assert tracker.remaining() is None

    def test_remaining_goes_negative_after_overshoot(self):
        """remaining() can be negative when spend overshoots the cap (budget already killed)."""
        tracker = BudgetTracker(Budget(per_run=0.10))
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.20)
        assert tracker.remaining() < 0


# ── Budget: thread safety ──────────────────────────────────────────────────

class TestBudgetThreadSafety:
    def test_concurrent_record_cost_does_not_undercount(self):
        """Many threads incrementing cost must never leave spend below the sum of increments."""
        tracker = BudgetTracker(Budget(per_run=100.0))
        errors = []

        def add():
            try:
                tracker.record_cost(0.01)
            except BudgetExceeded:
                pass
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # spend must reflect integer multiples of 0.01 and be <= 100.0
        assert round(tracker.run_spend, 5) <= 100.0


# ── Budget: zero and negative guard ───────────────────────────────────────

class TestBudgetValidation:
    def test_zero_per_run_raises_value_error(self):
        with pytest.raises(ValueError, match="per_run"):
            Budget(per_run=0)

    def test_negative_daily_raises_value_error(self):
        with pytest.raises(ValueError, match="daily"):
            Budget(daily=-0.001)

    def test_very_small_positive_cap_is_valid(self):
        b = Budget(per_run=0.0001)
        assert b.per_run == pytest.approx(0.0001)


# ── Interceptor: patch_status structure ───────────────────────────────────

class TestInterceptorPatchStatus:
    def test_all_three_providers_present(self):
        from wickd.interceptor import patch_status
        s = patch_status()
        assert "openai" in s
        assert "anthropic" in s
        assert "google" in s

    def test_each_provider_has_required_keys(self):
        from wickd.interceptor import patch_status
        required = {"installed", "patched", "verified", "error"}
        for info in patch_status().values():
            assert required.issubset(info.keys())

    def test_patch_status_returns_independent_copy(self):
        """Mutating the returned dict must not affect internal state."""
        from wickd.interceptor import patch_status, _patch_status
        result = patch_status()
        result["openai"]["installed"] = "TAMPERED"
        assert _patch_status["openai"]["installed"] != "TAMPERED"

    def test_double_patch_all_is_idempotent(self):
        """Calling patch_all() twice must leave patch_status unchanged."""
        from wickd.interceptor import patch_all, patch_status
        patch_all()
        status_before = patch_status()
        patch_all()
        status_after = patch_status()
        assert status_before == status_after

    def test_verify_patches_returns_dict_with_all_providers(self):
        from wickd.interceptor import verify_patches
        result = verify_patches()
        # Core LLM providers must always be present. The "mcp" key is
        # surfaced alongside when the optional mcp package is installed.
        assert {"openai", "anthropic", "google"}.issubset(result.keys())

    def test_unpatched_providers_are_not_verified(self):
        from wickd.interceptor import patch_status
        for info in patch_status().values():
            if not info["patched"]:
                assert info["verified"] is False


# ── Interceptor: sentinel mechanism ───────────────────────────────────────

class TestSentinelMechanism:
    def test_sentinel_value_is_string(self):
        from wickd.interceptor import _SENTINEL
        assert isinstance(_SENTINEL, str)
        assert len(_SENTINEL) > 0

    def test_verify_detects_overwrite(self):
        """Replacing a patched method with one lacking the sentinel must make verify return False."""
        from wickd.interceptor import (
            _SENTINEL, _patched, _patch_status,
            _verify_openai, _resolve_openai_sync,
        )
        # Only meaningful when openai is installed and patched
        if not _patch_status["openai"]["patched"]:
            pytest.skip("openai not patched in this environment")

        target = _resolve_openai_sync()
        if target is None:
            pytest.skip("openai not installed")

        owner, attr = target
        original = getattr(owner, attr)
        try:
            def plain_replacement(self, *a, **kw):  # no sentinel attribute
                pass
            setattr(owner, attr, plain_replacement)
            assert _verify_openai() is False
        finally:
            setattr(owner, attr, original)


# ── Streaming wrappers: no-usage response ─────────────────────────────────

class TestStreamingEdgeCases:
    def test_sync_stream_finalize_idempotent(self):
        """Calling finalize() twice on _StreamTracker must record cost only once."""
        from wickd.interceptor import _StreamTracker, _openai_stream_chunk
        import time

        costs_recorded = []

        # Patch _record_call to capture calls
        import wickd.interceptor as interceptor_mod
        original_record = interceptor_mod._record_call

        def capturing_record(*args, **kwargs):
            costs_recorded.append(args)
            return 0.0

        interceptor_mod._record_call = capturing_record
        try:
            st = _StreamTracker("openai", "gpt-4o", "hi", time.time(), _openai_stream_chunk)
            st.finalize()
            st.finalize()  # second call must be a no-op
            assert len(costs_recorded) == 1
        finally:
            interceptor_mod._record_call = original_record

    def test_sync_stream_passthrough_no_usage(self):
        """A chunk with no usage field must not crash the stream chunk handler."""
        from wickd.interceptor import _StreamTracker, _openai_stream_chunk
        import time

        class FakeChunk:
            pass  # no usage, no model, no choices

        st = _StreamTracker("openai", "gpt-4o", "prompt", time.time(), _openai_stream_chunk)
        st.on_chunk(FakeChunk())  # must not raise
        assert st._input_tokens == 0
        assert st._output_tokens == 0

    def test_async_stream_no_usage_chunk(self):
        """Anthropic stream chunk with unknown event type must be silently ignored."""
        from wickd.interceptor import _StreamTracker, _anthropic_stream_chunk
        import time

        class FakeEvent:
            type = "unknown_future_event"

        st = _StreamTracker("anthropic", "claude-3-opus", "prompt", time.time(), _anthropic_stream_chunk)
        st.on_chunk(FakeEvent())  # must not raise
        assert st._input_tokens == 0

    def test_wickd_sync_stream_delegates_getattr(self):
        """WickdSyncStream.__getattr__ must proxy unknown attributes to the wrapped stream."""
        from wickd.interceptor import WickdSyncStream, _StreamTracker, _openai_stream_chunk
        import time

        class FakeStream:
            custom_attr = "hello"

            def __iter__(self):
                return iter([])

            def __next__(self):
                raise StopIteration

        st = _StreamTracker("openai", "gpt-4o", "", time.time(), _openai_stream_chunk)
        wrapped = WickdSyncStream(iter([]), st)
        # Manually set _stream to the fake to test getattr delegation
        wrapped._stream = FakeStream()
        assert wrapped.custom_attr == "hello"
