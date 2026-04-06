"""Integration tests that make real LLM API calls.

Costs ~$0.01 per full run. Requires API keys in .env or environment.
Skipped automatically if keys are not present.
"""

import pytest
from tests.integration.conftest import skip_no_openai, skip_no_anthropic, skip_no_google

import wickd
from wickd.budget import Budget, BudgetExceeded
from wickd.interceptor import patch_all, patch_status, verify_patches


# ── OpenAI ──────────────────────────────────────────────────────────────────


@skip_no_openai
class TestOpenAIReal:
    def test_basic_call_tracks_cost(self):
        """A real OpenAI call should produce non-zero cost and token counts."""
        import openai

        trace_data = {}

        @wickd.agent(
            budget=Budget(per_run=1.00),
            on_run_complete=lambda e: trace_data.update(e),
        )
        def ask(question: str) -> str:
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": question}],
                max_tokens=10,
            )
            return resp.choices[0].message.content

        result = ask.run("Say hi")
        assert result is not None
        assert len(result) > 0
        summary = trace_data["summary"]
        assert summary["run_spend"] > 0
        assert summary["call_count"] == 1

    def test_streaming_tracks_cost(self):
        """A streaming OpenAI call should accumulate tokens correctly."""
        import openai

        trace_data = {}

        @wickd.agent(
            budget=Budget(per_run=1.00),
            on_run_complete=lambda e: trace_data.update(e),
        )
        def ask_stream(question: str) -> str:
            client = openai.OpenAI()
            chunks = []
            with client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": question}],
                max_tokens=10,
                stream=True,
            ) as stream:
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        chunks.append(chunk.choices[0].delta.content)
            return "".join(chunks)

        result = ask_stream.run("Say hello")
        assert result is not None
        assert len(result) > 0
        summary = trace_data["summary"]
        assert summary["run_spend"] > 0
        assert summary["call_count"] == 1

    def test_budget_kill_stops_agent(self):
        """A tiny budget should kill the agent on the first real call."""
        import openai

        @wickd.agent(budget=Budget(per_run=0.000001))
        def cheap_agent(question: str) -> str:
            client = openai.OpenAI()
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": question}],
                max_tokens=5,
            )
            return resp.choices[0].message.content

        with pytest.raises(BudgetExceeded):
            cheap_agent.run("Hi")


# ── Anthropic ───────────────────────────────────────────────────────────────


@skip_no_anthropic
class TestAnthropicReal:
    def test_basic_call_tracks_cost(self):
        """A real Anthropic call should produce non-zero cost."""
        import anthropic

        trace_data = {}

        @wickd.agent(
            budget=Budget(per_run=1.00),
            on_run_complete=lambda e: trace_data.update(e),
        )
        def ask(question: str) -> str:
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": question}],
            )
            return resp.content[0].text

        result = ask.run("Say hi")
        assert result is not None
        assert len(result) > 0
        summary = trace_data["summary"]
        assert summary["run_spend"] > 0
        assert summary["call_count"] == 1

    def test_streaming_tracks_cost(self):
        """A streaming Anthropic call should accumulate tokens."""
        import anthropic

        trace_data = {}

        @wickd.agent(
            budget=Budget(per_run=1.00),
            on_run_complete=lambda e: trace_data.update(e),
        )
        def ask_stream(question: str) -> str:
            client = anthropic.Anthropic()
            chunks = []
            with client.messages.stream(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{"role": "user", "content": question}],
            ) as stream:
                for text in stream.text_stream:
                    chunks.append(text)
            return "".join(chunks)

        result = ask_stream.run("Say hello")
        assert result is not None
        summary = trace_data["summary"]
        assert summary["run_spend"] > 0


# ── Google ──────────────────────────────────────────────────────────────────


@skip_no_google
class TestGoogleReal:
    def test_basic_call_tracks_cost(self):
        """A real Google Gemini call should produce non-zero cost."""
        from google import genai

        trace_data = {}

        @wickd.agent(
            budget=Budget(per_run=1.00),
            on_run_complete=lambda e: trace_data.update(e),
        )
        def ask(question: str) -> str:
            client = genai.Client()
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=question,
            )
            return resp.text

        result = ask.run("Say hi")
        assert result is not None
        assert len(result) > 0
        summary = trace_data["summary"]
        assert summary["run_spend"] > 0
        assert summary["call_count"] == 1


# ── Patch Verification ──────────────────────────────────────────────────────


@skip_no_openai
class TestPatchVerification:
    def test_patches_are_verified(self):
        """After patch_all(), installed providers should be verified."""
        patch_all()
        status = verify_patches()
        # OpenAI should be installed and verified
        assert status["openai"]["installed"] is True
        assert status["openai"]["verified"] is True
