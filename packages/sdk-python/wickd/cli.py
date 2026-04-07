"""Wickd CLI entry point. Parses arguments and dispatches to subcommand handlers."""

import argparse
import sys

from wickd.cli_traces import cmd_traces
from wickd.cli_approve import cmd_approve
from wickd.cli_status import cmd_status, cmd_version
from wickd.cli_init import cmd_init
from wickd.cli_watch import cmd_watch


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="wickd",
        description="Guardrails for AI agents.",
    )
    subparsers = parser.add_subparsers(dest="command")

    traces_parser = subparsers.add_parser("traces", help="View agent traces")
    traces_parser.add_argument("trace_id", nargs="?", help="Trace ID to view in detail")
    traces_parser.add_argument("--cost", action="store_true", help="Sort by cost (highest first)")
    traces_parser.add_argument("--limit", "-n", type=int, default=20, help="Max traces to show")
    traces_parser.set_defaults(func=cmd_traces)

    approve_parser = subparsers.add_parser("approve", help="Review pending approval requests")
    approve_parser.add_argument("--dir", help="Approval directory")
    approve_parser.set_defaults(func=cmd_approve)

    status_parser = subparsers.add_parser("status", help="Agent status summary")
    status_parser.set_defaults(func=cmd_status)

    init_parser = subparsers.add_parser("init", help="Scaffold a Wickd agent")
    init_parser.set_defaults(func=cmd_init)

    watch_parser = subparsers.add_parser("watch", help="Live-tail agent traces as they run")
    watch_parser.add_argument("--agent", help="Filter to a specific agent name")
    watch_parser.add_argument("--all", action="store_true", help="Show all traces (default: most recent)")
    watch_parser.set_defaults(func=cmd_watch)

    version_parser = subparsers.add_parser("version", help="Print version")
    version_parser.set_defaults(func=cmd_version)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
