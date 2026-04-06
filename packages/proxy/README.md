# wickd-proxy

LLM proxy server for Wickd — budget enforcement without monkey-patching.

```bash
pip install wickd-proxy
```

## Quick start

```bash
wickd-proxy start --budget-per-run 0.50 --budget-daily 5.00

# Point your SDK at the proxy
export OPENAI_BASE_URL=http://localhost:4319/openai/v1
export ANTHROPIC_BASE_URL=http://localhost:4319/anthropic/v1
```

Zero code changes. Works with any LLM SDK that supports custom base URLs.

## License

MIT
