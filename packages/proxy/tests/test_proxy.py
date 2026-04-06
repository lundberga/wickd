"""Tests for the Wickd LLM Proxy server."""

import logging
import os

import pytest
from starlette.testclient import TestClient

from wickd_proxy.config import ProxyConfig, BudgetConfig
from wickd_proxy.server import create_app, _extract_usage, ProxyState
from wickd_proxy import __version__


class TestExtractUsage:
    def test_openai(self):
        body = {"model": "gpt-4o", "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        model, inp, out = _extract_usage(body, "openai")
        assert model == "gpt-4o"
        assert inp == 100
        assert out == 50

    def test_anthropic(self):
        body = {"model": "claude-3-5-sonnet-20241022", "usage": {"input_tokens": 80, "output_tokens": 30}}
        model, inp, out = _extract_usage(body, "anthropic")
        assert model == "claude-3-5-sonnet-20241022"
        assert inp == 80
        assert out == 30

    def test_google(self):
        body = {"modelVersion": "gemini-2.0-pro", "usageMetadata": {"promptTokenCount": 60, "candidatesTokenCount": 25}}
        model, inp, out = _extract_usage(body, "google")
        assert model == "gemini-2.0-pro"
        assert inp == 60
        assert out == 25

    def test_missing_usage(self):
        model, inp, out = _extract_usage({"model": "gpt-4o"}, "openai")
        assert inp == 0 and out == 0


class TestConfig:
    def test_from_dict(self):
        config = ProxyConfig.from_dict({
            "port": 8080,
            "budget": {"per_run": 0.50, "daily": 5.00},
            "providers": {
                "openai": {"api_key": "sk-test"},
            },
        })
        assert config.port == 8080
        assert config.budget.per_run == 0.50
        assert config.budget.daily == 5.00
        assert config.providers["openai"].api_key == "sk-test"

    def test_from_args(self):
        config = ProxyConfig.from_args(
            port=9000,
            budget_per_run=1.0,
            budget_daily=10.0,
        )
        assert config.port == 9000
        assert config.budget.per_run == 1.0

    def test_defaults(self):
        config = ProxyConfig()
        assert config.port == 4319
        assert config.host == "127.0.0.1"
        assert config.budget.per_run is None

    def test_env_var_resolution(self, monkeypatch):
        monkeypatch.setenv("TEST_PROXY_API_KEY", "sk-from-env")
        config = ProxyConfig.from_dict({
            "providers": {"openai": {"api_key": "${TEST_PROXY_API_KEY}"}},
        })
        assert config.providers["openai"].api_key == "sk-from-env"

    def test_env_var_missing_logs_warning(self, monkeypatch, caplog):
        monkeypatch.delenv("MISSING_KEY_XYZ", raising=False)
        with caplog.at_level(logging.WARNING, logger="wickd"):
            config = ProxyConfig.from_dict({
                "providers": {"openai": {"api_key": "${MISSING_KEY_XYZ}"}},
            })
        assert config.providers["openai"].api_key == ""
        assert "MISSING_KEY_XYZ" in caplog.text


class TestHealthEndpoint:
    def test_health_no_budget(self):
        config = ProxyConfig()
        app = create_app(config)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_requests"] == 0
        assert data["total_cost"] == 0.0

    def test_health_with_budget(self):
        config = ProxyConfig.from_dict({"budget": {"per_run": 1.0, "daily": 10.0}})
        app = create_app(config)
        client = TestClient(app)
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
        assert "budget" in data
        assert data["budget"]["caps"]["per_run"] == 1.0


class TestStatusEndpoint:
    def test_status(self):
        config = ProxyConfig()
        app = create_app(config)
        client = TestClient(app)
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert data["version"] == __version__
        assert "upstreams" in data
        assert "openai" in data["upstreams"]

    def test_status_version_matches_package(self):
        config = ProxyConfig()
        app = create_app(config)
        client = TestClient(app)
        resp = client.get("/status")
        assert resp.json()["version"] == "0.3.0"


class TestBudgetEnforcement:
    def test_budget_exceeded_returns_429(self):
        config = ProxyConfig.from_dict({"budget": {"per_run": 0.001}})
        app = create_app(config)
        state: ProxyState = app.state.proxy
        # Manually exhaust the budget
        from wickd.budget import BudgetExceeded
        try:
            state.tracker.record_cost(0.01)
        except BudgetExceeded:
            pass

        client = TestClient(app)
        resp = client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 429
        assert "budget_exceeded" in resp.json()["error"]["type"]


class TestUnknownProvider:
    def test_unknown_provider_returns_400(self):
        # The route mounts only known providers, so an unknown path won't match
        # any provider route. We test _proxy_request directly via a known mount
        # with a spoofed provider by calling the internal helper.
        from wickd_proxy.server import _proxy_request
        # The app routing itself prevents unknown providers from reaching
        # _proxy_request, but we can verify the function returns 400.
        import asyncio
        from starlette.datastructures import Headers
        from starlette.requests import Request as StarletteRequest

        config = ProxyConfig()
        state = ProxyState(config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/unknown/v1/chat",
            "query_string": b"",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b"{}"}

        request = StarletteRequest(scope, receive)

        async def run():
            return await _proxy_request(request, "unknown_provider", state)

        resp = asyncio.get_event_loop().run_until_complete(run())
        assert resp.status_code == 400


class TestProxyStateClose:
    def test_close_closes_http_client(self):
        import asyncio
        config = ProxyConfig()
        state = ProxyState(config)
        assert not state.client.is_closed
        asyncio.get_event_loop().run_until_complete(state.close())
        assert state.client.is_closed


class TestNonJsonResponseHandling:
    def test_non_json_body_does_not_crash_proxy(self):
        """A non-JSON upstream response must not raise an exception."""
        from unittest.mock import AsyncMock, patch, MagicMock
        import httpx as _httpx

        config = ProxyConfig.from_dict({
            "providers": {"openai": {"api_key": "sk-test"}},
        })
        app = create_app(config)

        fake_response = _httpx.Response(
            200,
            content=b"not json",
            headers={"content-type": "text/plain"},
        )

        with patch.object(app.state.proxy.client, "request", new=AsyncMock(return_value=fake_response)):
            client = TestClient(app)
            resp = client.post(
                "/openai/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            )
        # Proxy should forward the response without crashing
        assert resp.status_code == 200
