import logging
import sys
import unittest.mock as mock

import pytest


def test_patch_error_field_populated_on_failure():
    """When a provider patch fails after import, _patch_status['error'] should contain the error message."""
    # Reset module state so we can trigger a fresh patch attempt
    import importlib
    import wickd.interceptor as interceptor_mod

    # Force the module to think openai is not yet patched
    original_patched = dict(interceptor_mod._patched)
    original_status = {k: dict(v) for k, v in interceptor_mod._patch_status.items()}

    try:
        interceptor_mod._patched["openai"] = False
        interceptor_mod._patch_status["openai"] = {
            "installed": False, "patched": False, "verified": False, "error": None
        }

        # Make resolve_target raise after the import succeeds
        def boom():
            raise RuntimeError("synthetic patch failure")

        provider = interceptor_mod._PROVIDERS[0]
        with mock.patch.object(provider, "resolve_target", boom):
            # Simulate the import succeeding by pre-marking installed
            interceptor_mod._patch_status["openai"]["installed"] = True
            interceptor_mod._patched["openai"] = False
            # Directly call _apply_patch; it should catch the error from resolve_target
            interceptor_mod._apply_patch(provider)

        assert interceptor_mod._patch_status["openai"]["error"] == "synthetic patch failure"
        assert interceptor_mod._patch_status["openai"]["patched"] is False
    finally:
        interceptor_mod._patched.update(original_patched)
        for k, v in original_status.items():
            interceptor_mod._patch_status[k].update(v)


def test_handler_exception_logged(caplog):
    """When on_run_complete raises, the warning is logged and the exception does not propagate."""
    from wickd.agent import WickdAgent

    def bad_fn():
        return "ok"

    def bad_handler(event):
        raise ValueError("handler boom")

    wa = WickdAgent(fn=bad_fn, auto_patch=False, on_run_complete=bad_handler)

    with caplog.at_level(logging.WARNING, logger="wickd"):
        result = wa.run()

    assert result == "ok"
    assert any("handler boom" in r.message for r in caplog.records)


def test_on_kill_exception_logged(caplog):
    """When Budget.on_kill raises, it is logged as a warning and does not mask BudgetExceeded."""
    from wickd.budget import Budget, BudgetTracker, BudgetExceeded

    def bad_on_kill(summary):
        raise RuntimeError("kill callback boom")

    budget = Budget(per_run=0.001, on_kill=bad_on_kill)
    tracker = BudgetTracker(budget)

    with caplog.at_level(logging.WARNING, logger="wickd"):
        with pytest.raises(BudgetExceeded):
            tracker.record_cost(0.01)

    assert any("kill callback boom" in r.message for r in caplog.records)
