"""Tests for patch verification and health check API."""

import pytest
from wickd.interceptor import (
    patch_status,
    verify_patches,
    status,
    _patch_status,
    _SENTINEL,
)


class TestPatchStatus:
    def test_returns_dict_per_provider(self):
        result = patch_status()
        for provider in ("openai", "anthropic", "google"):
            assert provider in result

    def test_each_provider_has_required_keys(self):
        for info in patch_status().values():
            assert "installed" in info
            assert "patched" in info
            assert "verified" in info
            assert "error" in info

    def test_returns_copy(self):
        result = patch_status()
        result["openai"]["installed"] = "MODIFIED"
        assert _patch_status["openai"]["installed"] != "MODIFIED"


class TestSentinel:
    def test_stamps_function(self):
        def dummy():
            pass
        setattr(dummy, _SENTINEL, True)
        assert getattr(dummy, _SENTINEL, False) is True

    def test_unstamped_lacks_sentinel(self):
        def dummy():
            pass
        assert getattr(dummy, _SENTINEL, False) is False


class TestVerifyPatches:
    def test_returns_status_dict(self):
        result = verify_patches()
        assert isinstance(result, dict)
        assert "openai" in result

    def test_unpatched_not_verified(self):
        for info in verify_patches().values():
            if not info["patched"]:
                assert info["verified"] is False


class TestStatus:
    def test_returns_patches_and_versions(self):
        result = status()
        assert "patches" in result
        assert "sdk_versions" in result
        assert isinstance(result["patches"], dict)
        assert isinstance(result["sdk_versions"], dict)
