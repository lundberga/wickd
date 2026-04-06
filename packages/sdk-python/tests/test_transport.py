"""Tests for transport-layer fallback (httpx interception)."""

from wickd.transport import _extract_usage, _provider_for_url, _should_intercept
from wickd.interceptor import _patch_status


class TestExtractUsage:
    def test_openai(self):
        body = {"model": "gpt-4o", "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        assert _extract_usage(body, "openai") == ("gpt-4o", 100, 50)

    def test_anthropic(self):
        body = {"model": "claude-3-5-sonnet-20241022", "usage": {"input_tokens": 80, "output_tokens": 30}}
        assert _extract_usage(body, "anthropic") == ("claude-3-5-sonnet-20241022", 80, 30)

    def test_google(self):
        body = {"modelVersion": "gemini-2.0-pro", "usageMetadata": {"promptTokenCount": 60, "candidatesTokenCount": 25}}
        assert _extract_usage(body, "google") == ("gemini-2.0-pro", 60, 25)

    def test_missing_usage(self):
        assert _extract_usage({"model": "gpt-4o"}, "openai") == ("gpt-4o", 0, 0)

    def test_unknown_provider(self):
        assert _extract_usage({}, "unknown") == ("unknown", 0, 0)


class TestProviderForUrl:
    def test_openai(self):
        assert _provider_for_url("https://api.openai.com/v1/chat/completions") == "openai"

    def test_anthropic(self):
        assert _provider_for_url("https://api.anthropic.com/v1/messages") == "anthropic"

    def test_google(self):
        assert _provider_for_url("https://generativelanguage.googleapis.com/v1/models") == "google"

    def test_unknown(self):
        assert _provider_for_url("https://example.com/api") is None


class TestShouldIntercept:
    def _with_status(self, provider, installed, verified):
        old = dict(_patch_status[provider])
        _patch_status[provider] = {"installed": installed, "patched": True, "verified": verified, "error": None}
        return old

    def test_not_installed(self):
        old = self._with_status("openai", False, False)
        try:
            assert _should_intercept("openai") is False
        finally:
            _patch_status["openai"] = old

    def test_installed_and_verified(self):
        old = self._with_status("openai", True, True)
        try:
            assert _should_intercept("openai") is False
        finally:
            _patch_status["openai"] = old

    def test_installed_not_verified(self):
        old = self._with_status("openai", True, False)
        try:
            assert _should_intercept("openai") is True
        finally:
            _patch_status["openai"] = old

    def test_unknown_provider(self):
        assert _should_intercept("unknown") is False
