"""
Intercepts LLM SDK calls for budget tracking, cost attribution, and tracing.

Patches OpenAI, Anthropic, and Google GenAI SDKs at the method level.
Falls back to httpx transport-layer interception when SDK patching fails.
"""

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("wickd")

from wickd.pricing import calculate_cost
from wickd.trace import Trace, TraceEvent

MAX_PREVIEW = 200
_SENTINEL = "_wickd_patched"

# ── Context ────────────────────────────────────────────────────────────────

_active_tracker: ContextVar[Optional[Any]] = ContextVar("wickd_tracker", default=None)
_active_trace: ContextVar[Optional[Trace]] = ContextVar("wickd_trace", default=None)


def set_active_tracker(tracker):
    _active_tracker.set(tracker)


def get_active_tracker():
    return _active_tracker.get()


def set_active_trace(trace: Optional[Trace]):
    _active_trace.set(trace)


def get_active_trace() -> Optional[Trace]:
    return _active_trace.get()


def _record_call(provider: str, model: str, input_tokens: int, output_tokens: int,
                 latency_ms: float, prompt_preview: str, response_preview: str) -> float:
    cost = calculate_cost(model, input_tokens, output_tokens)
    trace = get_active_trace()
    if trace:
        trace.add_llm_call(
            model=model, provider=provider,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost=cost, latency_ms=round(latency_ms, 1),
            prompt_preview=prompt_preview, response_preview=response_preview,
        )
    tracker = get_active_tracker()
    if tracker:
        tracker.record_cost(cost, model, input_tokens, output_tokens)
    return cost


# ── Provider registry ──────────────────────────────────────────────────────
# Each provider defines how to locate its SDK method, extract usage from
# responses and streaming chunks, and pull prompt/response previews.

def _safe_attr(obj, *attrs, default=None):
    """Walk a chain of getattr calls, returning default if any step is None."""
    for attr in attrs:
        obj = getattr(obj, attr, None)
        if obj is None:
            return default
    return obj


def _last_message_content(kwargs) -> str:
    """Extract the last user message from a messages-style request."""
    messages = kwargs.get("messages", [])
    if not messages or not isinstance(messages, list):
        return ""
    last = messages[-1]
    if not isinstance(last, dict):
        return ""
    content = last.get("content", "")
    if isinstance(content, str):
        return content[:MAX_PREVIEW]
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return str(block.get("text", ""))[:MAX_PREVIEW]
    return ""


# ── OpenAI ─────────────────────────────────────────────────────────────────

def _openai_usage(response):
    model = getattr(response, "model", "unknown")
    usage = getattr(response, "usage", None)
    if usage:
        return model, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
    return model, 0, 0


def _openai_response_text(response) -> str:
    msg = _safe_attr(response, "choices", default=[])
    if msg:
        return str(_safe_attr(msg[0], "message", "content", default=""))[:MAX_PREVIEW]
    return ""


def _openai_stream_chunk(tracker, chunk):
    usage = getattr(chunk, "usage", None)
    if usage:
        tracker._input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        tracker._output_tokens = getattr(usage, "completion_tokens", 0) or 0
    model = getattr(chunk, "model", None)
    if model:
        tracker._model = model
    if not tracker._response_preview:
        content = _safe_attr(chunk, "choices", default=[])
        if content:
            text = _safe_attr(content[0], "delta", "content")
            if text:
                tracker._response_preview = str(text)[:MAX_PREVIEW]


def _openai_pre_request(kwargs):
    """Inject stream_options so OpenAI returns usage in the final SSE chunk."""
    if kwargs.get("stream"):
        opts = kwargs.get("stream_options") or {}
        if not opts.get("include_usage"):
            kwargs["stream_options"] = {**opts, "include_usage": True}


# ── Anthropic ──────────────────────────────────────────────────────────────

def _anthropic_usage(response):
    model = getattr(response, "model", "unknown")
    usage = getattr(response, "usage", None)
    if usage:
        return model, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0
    return model, 0, 0


def _anthropic_response_text(response) -> str:
    content = getattr(response, "content", [])
    if content and isinstance(content, list):
        for block in content:
            if hasattr(block, "text"):
                return str(block.text)[:MAX_PREVIEW]
    return ""


def _anthropic_stream_chunk(tracker, event):
    event_type = getattr(event, "type", "")
    if event_type == "message_start":
        message = getattr(event, "message", None)
        if message:
            usage = getattr(message, "usage", None)
            if usage:
                tracker._input_tokens = getattr(usage, "input_tokens", 0) or 0
            model = getattr(message, "model", None)
            if model:
                tracker._model = model
    elif event_type == "message_delta":
        usage = getattr(event, "usage", None)
        if usage:
            tracker._output_tokens = getattr(usage, "output_tokens", 0) or 0
    elif event_type == "content_block_delta" and not tracker._response_preview:
        text = _safe_attr(event, "delta", "text")
        if text:
            tracker._response_preview = str(text)[:MAX_PREVIEW]


# ── Google GenAI ───────────────────────────────────────────────────────────

def _google_usage(response):
    usage = getattr(response, "usage_metadata", None)
    if usage:
        return getattr(usage, "prompt_token_count", 0) or 0, getattr(usage, "candidates_token_count", 0) or 0
    return 0, 0


def _google_prompt(kwargs, extra_args) -> str:
    contents = kwargs.get("contents") or (extra_args[0] if extra_args else None)
    if isinstance(contents, str):
        return contents[:MAX_PREVIEW]
    if isinstance(contents, list) and contents:
        first = contents[0]
        if isinstance(first, str):
            return first[:MAX_PREVIEW]
        if hasattr(first, "text"):
            return str(first.text)[:MAX_PREVIEW]
    return ""


def _google_response_text(response) -> str:
    try:
        return str(response.text)[:MAX_PREVIEW]
    except (AttributeError, ValueError):
        pass
    candidates = getattr(response, "candidates", [])
    if candidates:
        text = _safe_attr(candidates[0], "content", "parts", default=[])
        if text and hasattr(text[0], "text"):
            return str(text[0].text)[:MAX_PREVIEW]
    return ""


def _google_model_name(kwargs) -> str:
    name = kwargs.get("model", "gemini-unknown")
    if isinstance(name, str) and "/" in name:
        return name.rsplit("/", 1)[-1]
    return name


def _google_stream_chunk(tracker, chunk):
    usage = getattr(chunk, "usage_metadata", None)
    if usage:
        tracker._input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        tracker._output_tokens = getattr(usage, "candidates_token_count", 0) or 0
    if not tracker._response_preview:
        try:
            tracker._response_preview = str(chunk.text)[:MAX_PREVIEW]
        except (AttributeError, ValueError):
            pass


# ── Streaming wrappers ─────────────────────────────────────────────────────

class _StreamTracker:
    """Accumulates token usage from streaming chunks, records cost on completion."""

    def __init__(self, provider: str, model: str, prompt_preview: str,
                 start_time: float, chunk_handler: Callable):
        self._provider = provider
        self._model = model
        self._prompt_preview = prompt_preview
        self._start_time = start_time
        self._chunk_handler = chunk_handler
        self._input_tokens = 0
        self._output_tokens = 0
        self._response_preview = ""
        self._recorded = False

    def on_chunk(self, chunk):
        self._chunk_handler(self, chunk)

    def finalize(self):
        if self._recorded:
            return
        self._recorded = True
        latency_ms = (time.time() - self._start_time) * 1000
        _record_call(
            self._provider, self._model,
            self._input_tokens, self._output_tokens,
            latency_ms, self._prompt_preview, self._response_preview,
        )

    def __del__(self):
        # Best-effort: finalize if the iterator was abandoned without a context manager.
        try:
            self.finalize()
        except Exception:
            pass


class WickdSyncStream:
    """Wraps a sync streaming response, tracking cost on exhaustion."""

    def __init__(self, stream, tracker: _StreamTracker):
        self._stream = stream
        self._tracker = tracker

    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = next(self._stream)
        except StopIteration:
            self._tracker.finalize()
            raise
        except Exception:
            self._tracker.finalize()
            raise
        self._tracker.on_chunk(chunk)
        return chunk

    def __enter__(self):
        if hasattr(self._stream, "__enter__"):
            self._stream.__enter__()
        return self

    def __exit__(self, *args):
        self._tracker.finalize()
        if hasattr(self._stream, "__exit__"):
            return self._stream.__exit__(*args)
        return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


class WickdAsyncStream:
    """Wraps an async streaming response, tracking cost on exhaustion."""

    def __init__(self, stream, tracker: _StreamTracker):
        self._stream = stream
        self._tracker = tracker

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            self._tracker.finalize()
            raise
        except Exception:
            self._tracker.finalize()
            raise
        self._tracker.on_chunk(chunk)
        return chunk

    async def __aenter__(self):
        if hasattr(self._stream, "__aenter__"):
            await self._stream.__aenter__()
        return self

    async def __aexit__(self, *args):
        self._tracker.finalize()
        if hasattr(self._stream, "__aexit__"):
            return await self._stream.__aexit__(*args)
        return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _is_streaming(response) -> bool:
    return hasattr(response, "__iter__") and not isinstance(response, (str, bytes, dict, list))


def _is_async_streaming(response) -> bool:
    return hasattr(response, "__aiter__")


# ── Generic patcher ────────────────────────────────────────────────────────
# Eliminates the copy-paste between patch_openai/patch_anthropic/patch_google.

@dataclass
class _ProviderConfig:
    name: str
    import_path: str                                   # e.g. "openai"
    resolve_target: Callable                           # returns (sync_method_owner, attr_name) or None
    resolve_async_target: Optional[Callable] = None    # returns (async_method_owner, attr_name) or None
    extract_usage: Callable = None                     # (response) -> (model, in_tok, out_tok)
    extract_prompt: Callable = None                    # (kwargs, args) -> str
    extract_response: Callable = None                  # (response) -> str
    stream_chunk_handler: Callable = None              # (tracker, chunk) -> None
    pre_request_hook: Optional[Callable] = None        # (kwargs) -> None
    resolve_model: Optional[Callable] = None           # (kwargs) -> str
    verify: Optional[Callable] = None                  # () -> bool


def _make_sync_wrapper(original, provider: _ProviderConfig):
    """Build a sync interceptor for any provider."""

    def wrapper(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker:
            tracker.pre_call_check()
        if provider.pre_request_hook:
            provider.pre_request_hook(kwargs)

        start = time.time()
        response = original(self, *args, **kwargs)

        if kwargs.get("stream") and _is_streaming(response):
            model = (provider.resolve_model(kwargs) if provider.resolve_model
                     else kwargs.get("model", "unknown"))
            prompt = provider.extract_prompt(kwargs, args[1:] if len(args) > 1 else ())
            st = _StreamTracker(provider.name, model, prompt, start, provider.stream_chunk_handler)
            return WickdSyncStream(response, st)

        latency_ms = (time.time() - start) * 1000
        usage = provider.extract_usage(response)
        if len(usage) == 3:
            model, in_tok, out_tok = usage
        else:
            in_tok, out_tok = usage
            model = provider.resolve_model(kwargs) if provider.resolve_model else "unknown"
        prompt = provider.extract_prompt(kwargs, args[1:] if len(args) > 1 else ())
        resp_text = provider.extract_response(response)
        _record_call(provider.name, model, in_tok, out_tok, latency_ms, prompt, resp_text)
        return response

    setattr(wrapper, _SENTINEL, True)
    return wrapper


def _make_async_wrapper(original, provider: _ProviderConfig):
    """Build an async interceptor for any provider."""

    async def wrapper(self, *args, **kwargs):
        tracker = get_active_tracker()
        if tracker:
            tracker.pre_call_check()
        if provider.pre_request_hook:
            provider.pre_request_hook(kwargs)

        start = time.time()
        response = await original(self, *args, **kwargs)

        if kwargs.get("stream") and _is_async_streaming(response):
            model = (provider.resolve_model(kwargs) if provider.resolve_model
                     else kwargs.get("model", "unknown"))
            prompt = provider.extract_prompt(kwargs, args[1:] if len(args) > 1 else ())
            st = _StreamTracker(provider.name, model, prompt, start, provider.stream_chunk_handler)
            return WickdAsyncStream(response, st)

        latency_ms = (time.time() - start) * 1000
        usage = provider.extract_usage(response)
        if len(usage) == 3:
            model, in_tok, out_tok = usage
        else:
            in_tok, out_tok = usage
            model = provider.resolve_model(kwargs) if provider.resolve_model else "unknown"
        prompt = provider.extract_prompt(kwargs, args[1:] if len(args) > 1 else ())
        resp_text = provider.extract_response(response)
        _record_call(provider.name, model, in_tok, out_tok, latency_ms, prompt, resp_text)
        return response

    setattr(wrapper, _SENTINEL, True)
    return wrapper


# ── Patch state ────────────────────────────────────────────────────────────

_patch_status = {
    "openai": {"installed": False, "patched": False, "verified": False, "error": None},
    "anthropic": {"installed": False, "patched": False, "verified": False, "error": None},
    "google": {"installed": False, "patched": False, "verified": False, "error": None},
}
_patched = {"openai": False, "anthropic": False, "google": False}


def _apply_patch(config: _ProviderConfig):
    """Apply sync + async patches for a single provider."""
    name = config.name
    if _patched[name]:
        return

    try:
        __import__(config.import_path)
        _patch_status[name]["installed"] = True
    except ImportError:
        return

    # Sync
    try:
        target = config.resolve_target()
        if not target:
            return
        owner, attr = target
        original = getattr(owner, attr)
        setattr(owner, attr, _make_sync_wrapper(original, config))
    except Exception as e:
        _patch_status[name]["error"] = str(e)
        logger.warning("Failed to patch %s: %s", name, e)
        return

    # Async
    if config.resolve_async_target:
        try:
            async_target = config.resolve_async_target()
            if async_target:
                async_owner, async_attr = async_target
                async_original = getattr(async_owner, async_attr)
                setattr(async_owner, async_attr, _make_async_wrapper(async_original, config))
        except Exception as e:
            logger.warning("Failed to patch async %s: %s", name, e)

    _patched[name] = True
    _patch_status[name]["patched"] = True
    if config.verify:
        _patch_status[name]["verified"] = config.verify()
    logger.debug("Patched %s", name)


# ── Provider target resolvers ──────────────────────────────────────────────

def _resolve_openai_sync():
    try:
        import openai
        cls = openai.resources.chat.completions.Completions
        return cls, "create"
    except (ImportError, AttributeError):
        return None


def _resolve_openai_async():
    try:
        import openai
        cls = openai.resources.chat.completions.AsyncCompletions
        return cls, "create"
    except (ImportError, AttributeError):
        return None


def _resolve_anthropic_sync():
    try:
        import anthropic
        cls = anthropic.resources.messages.Messages
        return cls, "create"
    except (ImportError, AttributeError):
        return None


def _resolve_anthropic_async():
    try:
        import anthropic
        cls = anthropic.resources.messages.AsyncMessages
        return cls, "create"
    except (ImportError, AttributeError):
        return None


def _resolve_google_sync():
    try:
        from google.genai import models
        return models.Models, "generate_content"
    except (ImportError, AttributeError):
        return None


def _resolve_google_async():
    try:
        from google.genai import models
        return models.AsyncModels, "generate_content"
    except (ImportError, AttributeError):
        return None


# ── Verifiers ──────────────────────────────────────────────────────────────

def _verify_openai() -> bool:
    target = _resolve_openai_sync()
    return bool(target and getattr(getattr(target[0], target[1], None), _SENTINEL, False))


def _verify_anthropic() -> bool:
    target = _resolve_anthropic_sync()
    return bool(target and getattr(getattr(target[0], target[1], None), _SENTINEL, False))


def _verify_google() -> bool:
    target = _resolve_google_sync()
    return bool(target and getattr(getattr(target[0], target[1], None), _SENTINEL, False))


_VERIFIERS = {
    "openai": _verify_openai,
    "anthropic": _verify_anthropic,
    "google": _verify_google,
}

# ── Provider configs ───────────────────────────────────────────────────────

_PROVIDERS = [
    _ProviderConfig(
        name="openai",
        import_path="openai",
        resolve_target=_resolve_openai_sync,
        resolve_async_target=_resolve_openai_async,
        extract_usage=_openai_usage,
        extract_prompt=lambda kw, args: _last_message_content(kw),
        extract_response=_openai_response_text,
        stream_chunk_handler=_openai_stream_chunk,
        pre_request_hook=_openai_pre_request,
        verify=_verify_openai,
    ),
    _ProviderConfig(
        name="anthropic",
        import_path="anthropic",
        resolve_target=_resolve_anthropic_sync,
        resolve_async_target=_resolve_anthropic_async,
        extract_usage=_anthropic_usage,
        extract_prompt=lambda kw, args: _last_message_content(kw),
        extract_response=_anthropic_response_text,
        stream_chunk_handler=_anthropic_stream_chunk,
        verify=_verify_anthropic,
    ),
    _ProviderConfig(
        name="google",
        import_path="google.genai",
        resolve_target=_resolve_google_sync,
        resolve_async_target=_resolve_google_async,
        extract_usage=_google_usage,
        extract_prompt=lambda kw, args: _google_prompt(kw, args),
        extract_response=_google_response_text,
        stream_chunk_handler=_google_stream_chunk,
        resolve_model=_google_model_name,
        verify=_verify_google,
    ),
]


# ── Public API ─────────────────────────────────────────────────────────────

def patch_openai():
    _apply_patch(_PROVIDERS[0])


def patch_anthropic():
    _apply_patch(_PROVIDERS[1])


def patch_google():
    _apply_patch(_PROVIDERS[2])


def patch_all():
    """Patch all supported LLM SDKs, with transport-layer fallback."""
    for provider in _PROVIDERS:
        _apply_patch(provider)

    has_unverified = any(
        s["installed"] and not s["verified"] for s in _patch_status.values()
    )
    if has_unverified:
        try:
            from wickd.transport import patch_transport
            logger.warning("SDK patch verification failed for one or more providers; activating transport-layer fallback")
            patch_transport()
        except ImportError:
            pass


def patch_status() -> dict:
    """Current patch status per provider: {provider: {installed, patched, verified, error}}."""
    return {k: dict(v) for k, v in _patch_status.items()}


def verify_patches() -> dict:
    """Re-verify all patches are still active. Detects overwrites by other libraries."""
    for name, verifier in _VERIFIERS.items():
        if _patch_status[name]["patched"]:
            _patch_status[name]["verified"] = verifier()
    return patch_status()


def status() -> dict:
    """Full health check: patch status + detected SDK versions."""
    info = {"patches": verify_patches(), "sdk_versions": {}}
    for name in ("openai", "anthropic"):
        try:
            mod = __import__(name)
            info["sdk_versions"][name] = getattr(mod, "__version__", "unknown")
        except ImportError:
            pass
    try:
        import google.genai
        info["sdk_versions"]["google"] = getattr(google.genai, "__version__", "unknown")
    except ImportError:
        pass
    return info
