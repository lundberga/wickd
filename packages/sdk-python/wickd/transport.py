"""
Transport-layer fallback: intercepts at httpx when SDK-level patching fails.

Only activates for providers where SDK patches are NOT verified, preventing
double-counting. Does not declare httpx as a dependency — it's already a
transitive dep of the OpenAI and Anthropic SDKs.
"""

import json
import logging
import time
from typing import Optional

from wickd.interceptor import _patch_status, _record_call, get_active_tracker

logger = logging.getLogger("wickd")

PROVIDER_DOMAINS = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "google",
}

_transport_patched = False


def _provider_for_url(url: str) -> Optional[str]:
    for domain, name in PROVIDER_DOMAINS.items():
        if domain in url:
            return name
    return None


def _should_intercept(provider: str) -> bool:
    """Only intercept when the SDK patch failed for an installed provider."""
    info = _patch_status.get(provider, {})
    return info.get("installed", False) and not info.get("verified", False)


def _extract_usage(body: dict, provider: str) -> tuple:
    """Parse model and token counts from a JSON response body."""
    if provider == "openai":
        usage = body.get("usage", {})
        return body.get("model", "unknown"), usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    if provider == "anthropic":
        usage = body.get("usage", {})
        return body.get("model", "unknown"), usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    if provider == "google":
        meta = body.get("usageMetadata", {})
        return body.get("modelVersion", "gemini-unknown"), meta.get("promptTokenCount", 0), meta.get("candidatesTokenCount", 0)
    return "unknown", 0, 0


def _intercept_response(response, provider: str, start: float):
    """Extract usage from a completed HTTP response and record cost."""
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return
    try:
        body = response.json()
        model, in_tok, out_tok = _extract_usage(body, provider)
        _record_call(provider, model, in_tok, out_tok,
                     (time.time() - start) * 1000, "", "")
    except (json.JSONDecodeError, ValueError) as e:
        logger.debug("Transport fallback: failed to parse response: %s", e)


def patch_transport():
    """Patch httpx clients as a fallback for providers with broken SDK patches."""
    global _transport_patched
    if _transport_patched:
        return

    try:
        import httpx
    except ImportError:
        logger.warning("httpx not available; transport-layer fallback cannot activate")
        return

    orig_send = httpx.Client.send

    def patched_send(self, request, *args, **kwargs):
        provider = _provider_for_url(str(request.url))
        if not provider or not _should_intercept(provider):
            return orig_send(self, request, *args, **kwargs)

        tracker = get_active_tracker()
        if tracker:
            tracker.pre_call_check()

        start = time.time()
        response = orig_send(self, request, *args, **kwargs)
        _intercept_response(response, provider, start)
        return response

    httpx.Client.send = patched_send

    try:
        orig_async = httpx.AsyncClient.send

        async def patched_async_send(self, request, *args, **kwargs):
            provider = _provider_for_url(str(request.url))
            if not provider or not _should_intercept(provider):
                return await orig_async(self, request, *args, **kwargs)

            tracker = get_active_tracker()
            if tracker:
                tracker.pre_call_check()

            start = time.time()
            response = await orig_async(self, request, *args, **kwargs)
            _intercept_response(response, provider, start)
            return response

        httpx.AsyncClient.send = patched_async_send
    except AttributeError as e:
        logger.debug("Transport fallback: AsyncClient patch skipped: %s", e)

    _transport_patched = True
    logger.debug("Transport-layer fallback activated")
