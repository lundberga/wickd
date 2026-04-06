"""Shared fixtures for integration tests."""

import os
import pytest

# Load .env from repo root if present
_env_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                k, v = key.strip(), value.strip()
                if v and not os.environ.get(k):
                    os.environ[k] = v


def _has_key(name: str) -> bool:
    return bool(os.environ.get(name))


skip_no_openai = pytest.mark.skipif(
    not _has_key("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)

skip_no_anthropic = pytest.mark.skipif(
    not _has_key("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)

skip_no_google = pytest.mark.skipif(
    not _has_key("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set",
)
