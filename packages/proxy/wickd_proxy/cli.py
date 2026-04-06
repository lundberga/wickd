"""CLI entrypoint for wickd-proxy."""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="wickd-proxy",
        description="Wickd LLM Proxy — budget enforcement without monkey-patching",
    )
    parser.add_argument("command", choices=["start"], help="Command to run")
    parser.add_argument("--port", type=int, default=4319, help="Port to listen on (default: 4319)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--config", "-c", help="Path to wickd.yaml config file")
    parser.add_argument("--budget-per-run", type=float, help="Per-run budget cap in USD")
    parser.add_argument("--budget-daily", type=float, help="Daily budget cap in USD")
    parser.add_argument("--budget-monthly", type=float, help="Monthly budget cap in USD")

    args = parser.parse_args()

    if args.command == "start":
        _start(args)


def _start(args):
    from wickd_proxy.config import ProxyConfig
    from wickd_proxy.server import create_app

    # Load config from file or CLI args
    if args.config and os.path.exists(args.config):
        config = ProxyConfig.from_yaml(args.config)
    else:
        config = ProxyConfig.from_args(
            port=args.port,
            host=args.host,
            budget_per_run=args.budget_per_run,
            budget_daily=args.budget_daily,
            budget_monthly=args.budget_monthly,
        )

    # Override port/host from CLI if provided
    config.port = args.port
    config.host = args.host

    app = create_app(config)

    # Print startup info
    print(f"\033[32m[wickd-proxy]\033[0m Running on http://{config.host}:{config.port}", file=sys.stderr)
    print(f"  OpenAI:    http://{config.host}:{config.port}/openai/v1", file=sys.stderr)
    print(f"  Anthropic: http://{config.host}:{config.port}/anthropic/v1", file=sys.stderr)
    print(f"  Google:    http://{config.host}:{config.port}/google/v1", file=sys.stderr)
    print(f"  Health:    http://{config.host}:{config.port}/health", file=sys.stderr)

    if config.budget.per_run or config.budget.daily or config.budget.monthly:
        caps = []
        if config.budget.per_run:
            caps.append(f"per_run=${config.budget.per_run:.2f}")
        if config.budget.daily:
            caps.append(f"daily=${config.budget.daily:.2f}")
        if config.budget.monthly:
            caps.append(f"monthly=${config.budget.monthly:.2f}")
        print(f"  Budget:    {', '.join(caps)}", file=sys.stderr)
    else:
        print(f"  Budget:    none (tracking only)", file=sys.stderr)

    print(file=sys.stderr)

    import uvicorn
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
