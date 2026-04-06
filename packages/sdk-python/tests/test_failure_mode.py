"""Tests for configurable patch failure mode (on_patch_failure)."""

import warnings
import pytest
from wickd.agent import agent, WickdAgent
from wickd.budget import Budget, WickdPatchError


class TestOnPatchFailure:
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="on_patch_failure"):
            @agent(auto_patch=False, on_patch_failure="invalid")
            def my_agent(task: str):
                return task

    def test_allow_mode_runs_silently(self):
        """allow mode should not warn or raise, even without SDKs installed."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            @agent(auto_patch=True, on_patch_failure="allow")
            def my_agent(task: str):
                return task
            # Should not raise
            assert my_agent.run("test") == "test"

    def test_warn_mode_is_default(self):
        """Default mode is 'warn' — should not raise."""
        @agent(auto_patch=True)
        def my_agent(task: str):
            return task
        assert my_agent.run("test") == "test"

    def test_block_mode_with_no_patch_failures(self):
        """block mode should not raise when no SDKs are installed (nothing to fail)."""
        @agent(auto_patch=True, on_patch_failure="block")
        def my_agent(task: str):
            return task
        # No SDKs installed in test env = nothing patched = nothing to fail verification
        assert my_agent.run("test") == "test"


class TestWickdPatchError:
    def test_error_attributes(self):
        err = WickdPatchError(["openai", "anthropic"], {"openai": {"verified": False}})
        assert err.failed_providers == ["openai", "anthropic"]
        assert "openai" in str(err)
        assert "anthropic" in str(err)
        assert "Budget enforcement is NOT active" in str(err)

    def test_is_exception(self):
        err = WickdPatchError(["openai"], {})
        assert isinstance(err, Exception)
