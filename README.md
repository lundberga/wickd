# Wickd

Cost and control for custom AI agents.

See what your Python or TypeScript agent spends — across every LLM, every MCP tool, every run — and stop it before it burns you.

## Status

This repo is being rebuilt from scratch as **v1**. Nothing in `main` is usable yet.

Old releases (v0.x) are archived on the [`v0-legacy`](https://github.com/lundberga/wickd/tree/v0-legacy) branch and tagged [`v0.5.0-final`](https://github.com/lundberga/wickd/releases/tag/v0.5.0-final). They still install from PyPI and npm, but receive no further updates.

## What v1 will be

A single binary that sits between your agent and everything it calls:

```
agent ──▶ wickd ──▶ OpenAI / Anthropic / Google
                 ──▶ MCP servers (stdio or HTTP)
```

You set one environment variable. Your code does not change. Every LLM call, every MCP tool call, every cost, every latency gets recorded into one trace per agent run. You see it in a local dashboard. Budgets stop the run before it overspends.

Nothing else gives you a unified run trace across LLM and MCP today. That is the wedge.

## Roadmap

Built brick by brick. Each brick ships when it is rock solid.

1. **Core** — data model, SQLite storage, cost catalog
2. **Proxy** — LLM HTTP proxy + MCP stdio/HTTP proxy, single binary
3. **SDK** — thin Python and TypeScript clients for advanced inline use
4. **Dashboard** — local web UI, bundled into the proxy binary
5. **Cloud** — hosted team dashboard, shared budgets, audit log
6. **Plugins** — Claude Code, Cursor, MCP directory integrations

## License

MIT. See [LICENSE](LICENSE).
