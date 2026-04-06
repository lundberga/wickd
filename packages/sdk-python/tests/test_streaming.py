"""Tests for streaming response interception and cost tracking."""

import time
import pytest
from unittest.mock import MagicMock
from wickd.interceptor import (
    WickdSyncStream,
    WickdAsyncStream,
    _StreamTracker,
    _openai_stream_chunk,
    _anthropic_stream_chunk,
    _google_stream_chunk,
    _openai_pre_request,
    _record_call,
    set_active_tracker,
    set_active_trace,
)
from wickd.budget import Budget, BudgetTracker
from wickd.trace import Trace


def _make_openai_chunks(model="gpt-4o", text="Hello world", prompt_tokens=10, completion_tokens=5):
    c1 = MagicMock()
    c1.model = model
    c1.usage = None
    c1.choices = [MagicMock()]
    c1.choices[0].delta = MagicMock()
    c1.choices[0].delta.content = text

    c2 = MagicMock()
    c2.model = model
    c2.usage = None
    c2.choices = [MagicMock()]
    c2.choices[0].delta = MagicMock()
    c2.choices[0].delta.content = " more"

    c3 = MagicMock()
    c3.model = model
    c3.usage = MagicMock()
    c3.usage.prompt_tokens = prompt_tokens
    c3.usage.completion_tokens = completion_tokens
    c3.choices = []
    return [c1, c2, c3]


def _make_anthropic_chunks(model="claude-3-5-sonnet-20241022", text="Hi", input_tokens=8, output_tokens=3):
    msg_start = MagicMock()
    msg_start.type = "message_start"
    msg_start.message = MagicMock()
    msg_start.message.usage = MagicMock()
    msg_start.message.usage.input_tokens = input_tokens
    msg_start.message.model = model

    content_delta = MagicMock()
    content_delta.type = "content_block_delta"
    content_delta.delta = MagicMock()
    content_delta.delta.text = text

    msg_delta = MagicMock()
    msg_delta.type = "message_delta"
    msg_delta.usage = MagicMock()
    msg_delta.usage.output_tokens = output_tokens
    return [msg_start, content_delta, msg_delta]


def _make_google_chunks(text="Result", prompt_count=12, candidates_count=6):
    c1 = MagicMock()
    c1.text = text
    c1.usage_metadata = None

    c2 = MagicMock()
    c2.text = " more"
    c2.usage_metadata = MagicMock()
    c2.usage_metadata.prompt_token_count = prompt_count
    c2.usage_metadata.candidates_token_count = candidates_count
    return [c1, c2]


def _tracker(handler, provider="openai", model="gpt-4o"):
    return _StreamTracker(provider, model, "test prompt", time.time(), handler)


class TestOpenAIStreamTracking:
    def test_sync_stream_tracks_cost(self):
        t = _tracker(_openai_stream_chunk)
        chunks = _make_openai_chunks(prompt_tokens=100, completion_tokens=50)
        collected = list(WickdSyncStream(iter(chunks), t))
        assert len(collected) == 3
        assert t._input_tokens == 100
        assert t._output_tokens == 50
        assert t._recorded is True

    def test_preserves_chunks(self):
        t = _tracker(_openai_stream_chunk)
        chunks = _make_openai_chunks()
        collected = list(WickdSyncStream(iter(chunks), t))
        assert collected[0].choices[0].delta.content == "Hello world"

    def test_captures_response_preview(self):
        t = _tracker(_openai_stream_chunk)
        list(WickdSyncStream(iter(_make_openai_chunks(text="First chunk")), t))
        assert t._response_preview == "First chunk"

    def test_context_manager(self):
        t = _tracker(_openai_stream_chunk)
        stream = WickdSyncStream(iter(_make_openai_chunks()), t)
        with stream as s:
            next(s)
        assert t._recorded is True

    def test_empty_stream(self):
        t = _tracker(_openai_stream_chunk)
        assert list(WickdSyncStream(iter([]), t)) == []
        assert t._recorded is True

    def test_budget_tracker_integration(self):
        budget_tracker = BudgetTracker(Budget(per_run=1.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="stream"))
        try:
            t = _tracker(_openai_stream_chunk)
            list(WickdSyncStream(iter(_make_openai_chunks(prompt_tokens=100, completion_tokens=50)), t))
            assert budget_tracker.run_spend > 0
            assert budget_tracker.call_count == 1
        finally:
            set_active_tracker(None)
            set_active_trace(None)


class TestAnthropicStreamTracking:
    def test_tracks_cost(self):
        t = _tracker(_anthropic_stream_chunk, "anthropic", "unknown")
        list(WickdSyncStream(iter(_make_anthropic_chunks(input_tokens=50, output_tokens=25)), t))
        assert t._input_tokens == 50
        assert t._output_tokens == 25
        assert t._model == "claude-3-5-sonnet-20241022"

    def test_captures_preview(self):
        t = _tracker(_anthropic_stream_chunk, "anthropic")
        list(WickdSyncStream(iter(_make_anthropic_chunks(text="Response")), t))
        assert t._response_preview == "Response"


class TestGoogleStreamTracking:
    def test_tracks_cost(self):
        t = _tracker(_google_stream_chunk, "google", "gemini-2.0-pro")
        list(WickdSyncStream(iter(_make_google_chunks(prompt_count=80, candidates_count=40)), t))
        assert t._input_tokens == 80
        assert t._output_tokens == 40

    def test_captures_preview(self):
        t = _tracker(_google_stream_chunk, "google")
        list(WickdSyncStream(iter(_make_google_chunks(text="Google says")), t))
        assert t._response_preview == "Google says"


class TestAsyncStreamTracking:
    @pytest.mark.asyncio
    async def test_async_stream(self):
        t = _tracker(_openai_stream_chunk)
        chunks = _make_openai_chunks(prompt_tokens=200, completion_tokens=100)

        async def gen():
            for c in chunks:
                yield c

        collected = [c async for c in WickdAsyncStream(gen(), t)]
        assert len(collected) == 3
        assert t._input_tokens == 200
        assert t._recorded is True

    @pytest.mark.asyncio
    async def test_async_empty(self):
        t = _tracker(_openai_stream_chunk)

        async def gen():
            return
            yield

        assert [c async for c in WickdAsyncStream(gen(), t)] == []
        assert t._recorded is True


class TestOpenAIStreamOptions:
    def test_injects_when_streaming(self):
        kw = {"stream": True, "model": "gpt-4o"}
        _openai_pre_request(kw)
        assert kw["stream_options"] == {"include_usage": True}

    def test_preserves_existing(self):
        kw = {"stream": True, "stream_options": {"include_usage": True, "custom": "v"}}
        _openai_pre_request(kw)
        assert kw["stream_options"]["custom"] == "v"

    def test_no_inject_without_stream(self):
        kw = {"model": "gpt-4o"}
        _openai_pre_request(kw)
        assert "stream_options" not in kw


class TestFinalizeIdempotency:
    def test_double_finalize(self):
        budget_tracker = BudgetTracker(Budget(per_run=10.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="test"))
        try:
            t = _tracker(_openai_stream_chunk)
            list(WickdSyncStream(iter(_make_openai_chunks()), t))
            t.finalize()  # second call — should be no-op
            assert budget_tracker.call_count == 1
        finally:
            set_active_tracker(None)
            set_active_trace(None)
