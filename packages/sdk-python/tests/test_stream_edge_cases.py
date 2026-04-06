"""Tests for stream edge cases: mid-iteration errors, abandoned iterators, idempotent finalize."""

import time
import pytest
from unittest.mock import MagicMock
from wickd.interceptor import (
    WickdSyncStream,
    WickdAsyncStream,
    _StreamTracker,
    _openai_stream_chunk,
)
from wickd.budget import Budget, BudgetTracker
from wickd.trace import Trace
from wickd.interceptor import set_active_tracker, set_active_trace


def _tracker(handler=None, provider="openai", model="gpt-4o"):
    return _StreamTracker(provider, model, "prompt", time.time(), handler or _openai_stream_chunk)


def _make_chunk(prompt_tokens=10, completion_tokens=5):
    c = MagicMock()
    c.model = "gpt-4o"
    c.usage = MagicMock()
    c.usage.prompt_tokens = prompt_tokens
    c.usage.completion_tokens = completion_tokens
    c.choices = []
    return c


def _make_plain_chunk():
    c = MagicMock()
    c.model = "gpt-4o"
    c.usage = None
    c.choices = [MagicMock()]
    c.choices[0].delta = MagicMock()
    c.choices[0].delta.content = "hi"
    return c


class BombIterator:
    """Yields n chunks then raises RuntimeError."""

    def __init__(self, chunks, after: int):
        self._chunks = chunks
        self._after = after
        self._count = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._count < self._after:
            self._count += 1
            return self._chunks[self._count - 1]
        raise RuntimeError("mid-stream failure")


class AsyncBombIterator:
    """Async version: yields n chunks then raises RuntimeError."""

    def __init__(self, chunks, after: int):
        self._chunks = chunks
        self._after = after
        self._count = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._count < self._after:
            self._count += 1
            return self._chunks[self._count - 1]
        raise RuntimeError("async mid-stream failure")


class TestSyncStreamExceptionFinalizes:
    def test_exception_mid_iteration_finalizes(self):
        budget_tracker = BudgetTracker(Budget(per_run=10.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="edge"))
        try:
            chunks = [_make_plain_chunk(), _make_plain_chunk(), _make_chunk(10, 5)]
            bomb = BombIterator(chunks, after=2)
            t = _tracker()
            stream = WickdSyncStream(bomb, t)

            collected = []
            with pytest.raises(RuntimeError, match="mid-stream failure"):
                for chunk in stream:
                    collected.append(chunk)

            assert len(collected) == 2
            assert t._recorded is True
            # Cost is still recorded (0 tokens because the usage chunk wasn't reached,
            # but finalize was called — that's the contract we're testing).
            assert budget_tracker.call_count == 1
        finally:
            set_active_tracker(None)
            set_active_trace(None)

    def test_exception_after_usage_chunk_records_cost(self):
        budget_tracker = BudgetTracker(Budget(per_run=10.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="edge"))
        try:
            # Usage chunk comes before the error.
            chunks = [_make_chunk(100, 50), _make_plain_chunk()]
            bomb = BombIterator(chunks, after=1)
            t = _tracker()
            stream = WickdSyncStream(bomb, t)

            with pytest.raises(RuntimeError):
                list(stream)

            assert t._recorded is True
            assert t._input_tokens == 100
            assert t._output_tokens == 50
            assert budget_tracker.run_spend > 0
        finally:
            set_active_tracker(None)
            set_active_trace(None)


class TestAsyncStreamExceptionFinalizes:
    @pytest.mark.asyncio
    async def test_exception_mid_iteration_finalizes(self):
        budget_tracker = BudgetTracker(Budget(per_run=10.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="edge"))
        try:
            chunks = [_make_plain_chunk(), _make_plain_chunk()]
            bomb = AsyncBombIterator(chunks, after=2)
            t = _tracker()
            stream = WickdAsyncStream(bomb, t)

            collected = []
            with pytest.raises(RuntimeError, match="async mid-stream failure"):
                async for chunk in stream:
                    collected.append(chunk)

            assert len(collected) == 2
            assert t._recorded is True
            assert budget_tracker.call_count == 1
        finally:
            set_active_tracker(None)
            set_active_trace(None)

    @pytest.mark.asyncio
    async def test_exception_after_usage_chunk_records_cost(self):
        budget_tracker = BudgetTracker(Budget(per_run=10.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="edge"))
        try:
            chunks = [_make_chunk(80, 40), _make_plain_chunk()]
            bomb = AsyncBombIterator(chunks, after=1)
            t = _tracker()
            stream = WickdAsyncStream(bomb, t)

            with pytest.raises(RuntimeError):
                async for _ in stream:
                    pass

            assert t._recorded is True
            assert t._input_tokens == 80
            assert t._output_tokens == 40
            assert budget_tracker.run_spend > 0
        finally:
            set_active_tracker(None)
            set_active_trace(None)


class TestSyncStreamBreakWithContextManager:
    def test_break_inside_with_block_finalizes(self):
        budget_tracker = BudgetTracker(Budget(per_run=10.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="edge"))
        try:
            chunks = [_make_plain_chunk(), _make_chunk(10, 5), _make_plain_chunk()]
            t = _tracker()
            stream = WickdSyncStream(iter(chunks), t)

            with stream as s:
                next(s)
                # Break out early — __exit__ must still finalize.

            assert t._recorded is True
            assert budget_tracker.call_count == 1
        finally:
            set_active_tracker(None)
            set_active_trace(None)


class TestFinalizeIdempotent:
    def test_calling_finalize_twice_does_not_double_count(self):
        budget_tracker = BudgetTracker(Budget(per_run=10.0))
        set_active_tracker(budget_tracker)
        set_active_trace(Trace(agent_name="test", task="edge"))
        try:
            t = _tracker()
            chunks = [_make_chunk(50, 25)]
            list(WickdSyncStream(iter(chunks), t))

            assert budget_tracker.call_count == 1
            first_spend = budget_tracker.run_spend

            t.finalize()  # second call — must be a no-op
            t.finalize()  # third call — also no-op

            assert budget_tracker.call_count == 1
            assert budget_tracker.run_spend == first_spend
        finally:
            set_active_tracker(None)
            set_active_trace(None)
