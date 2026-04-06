"""Tests for the Wickd LLM Proxy server."""

import pytest
from starlette.testclient import TestClient

from wickd_proxy.config import ProxyConfig, BudgetConfig
from wickd_proxy.server import create_app, _extract_usage, ProxyState


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
        assert "upstreams" in data
        assert "openai" in data["upstreams"]


class TestBudgetEnforcement:
    def test_budget_exceeded_returns_429(self):
        config = ProxyConfig.from_dict({"budget": {"per_run": 0.001}})
        app = create_app(config)
        state: ProxyState = app.state.proxy
        # Manually exhaust the budget
        from wickd.budget import BudgetExceeded
        try:
            state.tracker.record_cost(0.01, "gpt-4o", 100, 50)
        except BudgetExceeded:
            pass

        client = TestClient(app)
        # Next request should be rejected
        resp = client.post(
            "/openai/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 429
        assert "budget_exceeded" in resp.json()["error"]["type"]
