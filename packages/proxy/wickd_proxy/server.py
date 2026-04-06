"""
Wickd LLM Proxy Server.

A reverse proxy that sits between your agent code and LLM providers.
Enforces budgets, tracks cost, emits traces — without any monkey-patching.

Usage:
    client = OpenAI(base_url="http://localhost:4319/openai/v1")
    # Or: export OPENAI_BASE_URL=http://localhost:4319/openai/v1
"""

import json
import time
from typing import Optional

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse, JSONResponse
from starlette.routing import Route, Mount

from wickd.budget import Budget, BudgetTracker, BudgetExceeded
from wickd.pricing import calculate_cost
from wickd.trace import Trace, TraceStore

from wickd_proxy.config import ProxyConfig

# Provider upstream URLs
PROVIDER_UPSTREAMS = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
}


class ProxyState:
    """Shared state for the proxy server."""

    def __init__(self, config: ProxyConfig):
        self.config = config
        self.budget = Budget(
            per_run=config.budget.per_run,
            daily=config.budget.daily,
            monthly=config.budget.monthly,
        ) if any([config.budget.per_run, config.budget.daily, config.budget.monthly]) else None
        self.tracker = BudgetTracker(self.budget) if self.budget else None
        self.trace_store = TraceStore()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        self.total_requests = 0
        self.total_cost = 0.0

    async def close(self):
        await self.client.aclose()


def _extract_usage(body: dict, provider: str) -> tuple[str, int, int]:
    """Extract model, input_tokens, output_tokens from response JSON."""
    if provider == "openai":
        model = body.get("model", "unknown")
        usage = body.get("usage", {})
        return model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    elif provider == "anthropic":
        model = body.get("model", "unknown")
        usage = body.get("usage", {})
        return model, usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    elif provider == "google":
        metadata = body.get("usageMetadata", {})
        model = body.get("modelVersion", "gemini-unknown")
        return model, metadata.get("promptTokenCount", 0), metadata.get("candidatesTokenCount", 0)
    return "unknown", 0, 0


async def _proxy_request(request: Request, provider: str, state: ProxyState) -> Response:
    """Forward a request to the upstream provider, tracking cost."""
    upstream_base = PROVIDER_UPSTREAMS.get(provider)
    if not upstream_base:
        return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=400)

    # Budget pre-check
    if state.tracker:
        try:
            state.tracker.pre_call_check()
        except BudgetExceeded as e:
            return JSONResponse({
                "error": {
                    "message": f"Wickd budget exceeded: {e}",
                    "type": "budget_exceeded",
                    "code": "budget_exceeded",
                }
            }, status_code=429)

    # Build upstream URL
    # Request path is like /openai/v1/chat/completions
    # We need to strip the /openai prefix
    path = request.url.path
    prefix = f"/{provider}"
    if path.startswith(prefix):
        path = path[len(prefix):]

    upstream_url = f"{upstream_base}{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    # Forward headers, inject API key if configured
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    provider_config = state.config.providers.get(provider)
    if provider_config and provider_config.api_key:
        if provider == "openai":
            headers["authorization"] = f"Bearer {provider_config.api_key}"
        elif provider == "anthropic":
            headers["x-api-key"] = provider_config.api_key
        elif provider == "google":
            # Google uses query param or header
            headers["x-goog-api-key"] = provider_config.api_key

    body = await request.body()
    start = time.time()

    # Check if streaming
    is_streaming = False
    try:
        req_json = json.loads(body)
        is_streaming = req_json.get("stream", False)
    except (json.JSONDecodeError, ValueError):
        pass

    if is_streaming:
        return await _proxy_streaming(upstream_url, headers, body, provider, state, start)

    # Non-streaming: forward, parse, record
    try:
        resp = await state.client.request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
    except httpx.HTTPError as e:
        return JSONResponse({"error": {"message": str(e), "type": "proxy_error"}}, status_code=502)

    latency_ms = (time.time() - start) * 1000

    # Track cost from response
    if "application/json" in resp.headers.get("content-type", ""):
        try:
            resp_body = resp.json()
            model, in_tok, out_tok = _extract_usage(resp_body, provider)
            cost = calculate_cost(model, in_tok, out_tok)
            if state.tracker:
                try:
                    state.tracker.record_cost(cost, model, in_tok, out_tok)
                except BudgetExceeded:
                    pass  # Will be caught on next pre_call_check
            state.total_cost += cost
            state.total_requests += 1
        except (json.JSONDecodeError, ValueError):
            pass

    # Forward response headers
    resp_headers = dict(resp.headers)
    resp_headers.pop("content-encoding", None)
    resp_headers.pop("transfer-encoding", None)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
    )


async def _proxy_streaming(
    upstream_url: str, headers: dict, body: bytes,
    provider: str, state: ProxyState, start: float,
) -> StreamingResponse:
    """Forward a streaming request, accumulating usage from SSE chunks."""

    async def stream_generator():
        accumulated_input = 0
        accumulated_output = 0
        model = "unknown"

        async with state.client.stream(
            "POST", upstream_url, headers=headers, content=body,
        ) as resp:
            async for line in resp.aiter_lines():
                yield line + "\n"

                # Parse SSE data lines for usage
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        chunk = json.loads(line[6:])
                        if provider == "openai":
                            usage = chunk.get("usage")
                            if usage:
                                accumulated_input = usage.get("prompt_tokens", 0)
                                accumulated_output = usage.get("completion_tokens", 0)
                            if chunk.get("model"):
                                model = chunk["model"]
                        elif provider == "anthropic":
                            event_type = chunk.get("type", "")
                            if event_type == "message_start":
                                msg = chunk.get("message", {})
                                usage = msg.get("usage", {})
                                accumulated_input = usage.get("input_tokens", 0)
                                if msg.get("model"):
                                    model = msg["model"]
                            elif event_type == "message_delta":
                                usage = chunk.get("usage", {})
                                accumulated_output = usage.get("output_tokens", 0)
                    except (json.JSONDecodeError, ValueError):
                        pass

            # Record cost on stream completion
            cost = calculate_cost(model, accumulated_input, accumulated_output)
            if state.tracker and cost > 0:
                try:
                    state.tracker.record_cost(cost, model, accumulated_input, accumulated_output)
                except BudgetExceeded:
                    pass
            state.total_cost += cost
            state.total_requests += 1

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


async def health_endpoint(request: Request) -> JSONResponse:
    """Health check endpoint with budget status."""
    state: ProxyState = request.app.state.proxy
    data = {
        "status": "ok",
        "total_requests": state.total_requests,
        "total_cost": round(state.total_cost, 6),
    }
    if state.tracker:
        data["budget"] = state.tracker.summary()
    return JSONResponse(data)


async def status_endpoint(request: Request) -> JSONResponse:
    """Detailed status endpoint."""
    state: ProxyState = request.app.state.proxy
    return JSONResponse({
        "version": "0.1.0",
        "upstreams": PROVIDER_UPSTREAMS,
        "providers_configured": list(state.config.providers.keys()),
        "budget": state.tracker.summary() if state.tracker else None,
        "total_requests": state.total_requests,
        "total_cost": round(state.total_cost, 6),
    })


def _make_provider_route(provider: str):
    async def handler(request: Request) -> Response:
        return await _proxy_request(request, provider, request.app.state.proxy)
    return handler


def create_app(config: ProxyConfig) -> Starlette:
    """Create the Starlette proxy application."""
    state = ProxyState(config)

    routes = [
        Route("/health", health_endpoint),
        Route("/status", status_endpoint),
        # Provider routes — match everything under /{provider}/
        Mount("/openai", routes=[Route("/{path:path}", _make_provider_route("openai"), methods=["GET", "POST", "PUT", "DELETE", "PATCH"])]),
        Mount("/anthropic", routes=[Route("/{path:path}", _make_provider_route("anthropic"), methods=["GET", "POST", "PUT", "DELETE", "PATCH"])]),
        Mount("/google", routes=[Route("/{path:path}", _make_provider_route("google"), methods=["GET", "POST", "PUT", "DELETE", "PATCH"])]),
    ]

    app = Starlette(routes=routes)
    app.state.proxy = state

    @app.on_event("shutdown")
    async def shutdown():
        await state.close()

    return app
