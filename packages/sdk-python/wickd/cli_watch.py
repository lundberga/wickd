"""wickd watch subcommand."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from wickd.trace import TraceStore
from wickd.cli_render import (
    _C, format_cost, format_cost_raw, format_status, format_duration, progress_bar
)


def _render_watch_trace(trace: dict) -> None:
    """Render a single trace for the watch detail view."""
    agent_name = trace.get("agent_name", "?")
    status = trace.get("status", "?")
    cost_val = trace.get("total_cost", 0)
    duration = trace.get("duration_ms")
    started = trace.get("started_at", "")

    if status == "running" and started:
        try:
            dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            duration = (time.time() - dt.timestamp()) * 1000
        except (ValueError, AttributeError):
            pass

    print(f"{_C.BOLD}{'=' * 64}{_C.RESET}")
    print(f"  {_C.BCYAN}WICKD WATCH{_C.RESET}  |  {_C.BOLD}{agent_name}{_C.RESET}  |  {format_status(status)}  |  {format_duration(duration)}")
    print(f"{_C.BOLD}{'=' * 64}{_C.RESET}")

    budget = trace.get("budget_summary")
    if budget:
        caps = budget.get("caps", {})
        if caps.get("per_run"):
            spend = budget.get("run_spend", cost_val)
            cap = caps["per_run"]
            print(f"\n  {_C.BOLD}Budget:{_C.RESET} ${spend:.4f} / ${cap:.2f}  {progress_bar(spend, cap, width=30)}")

    print(
        f"\n  {_C.BOLD}Total cost:{_C.RESET} {format_cost(cost_val)}    "
        f"{_C.BOLD}Tokens:{_C.RESET} {trace.get('total_input_tokens', 0):,} in / {trace.get('total_output_tokens', 0):,} out    "
        f"{_C.BOLD}Calls:{_C.RESET} {trace.get('llm_calls', 0)}"
    )

    events = trace.get("events", [])
    if events:
        print(f"\n  {_C.DIM}{'-' * 60}{_C.RESET}")
        for i, event in enumerate(events):
            _render_watch_event(i, event)

    print(f"\n  {_C.DIM}Updated: {datetime.now().strftime('%H:%M:%S')} | Ctrl+C to exit{_C.RESET}")
    print(f"{_C.BOLD}{'=' * 64}{_C.RESET}")


def _render_watch_event(i: int, event: dict) -> None:
    etype = event.get("event_type", "?")

    if etype == "llm_call":
        model = event.get("model", "?")
        cost = event.get("cost", 0)
        tokens_in = event.get("input_tokens", 0)
        tokens_out = event.get("output_tokens", 0)
        latency = event.get("latency_ms", 0)
        print(
            f"  {_C.CYAN}{i+1:>3}.{_C.RESET} {_C.BOLD}LLM{_C.RESET} {model:<28} "
            f"{format_cost(cost):>20}  {tokens_in:>6,}+{tokens_out:<6,} tok  {format_duration(latency):>8}"
        )

    elif etype == "approval_gate":
        name = event.get("approval_name", "?")
        astatus = event.get("approval_status", "?")
        if astatus == "approved":
            badge = f"{_C.BGREEN}APPROVED{_C.RESET}"
        elif astatus == "denied":
            badge = f"{_C.BRED}DENIED{_C.RESET}"
        else:
            badge = f"{_C.BYELLOW}PENDING{_C.RESET}"
        print(f"  {_C.CYAN}{i+1:>3}.{_C.RESET} {_C.BOLD}APPROVAL{_C.RESET} {name:<24} {badge}")

    elif etype == "budget_kill":
        trigger = event.get("budget_trigger", "?")
        spend = event.get("spend_at_event", 0)
        print(f"  {_C.CYAN}{i+1:>3}.{_C.RESET} {_C.BRED}BUDGET KILL{_C.RESET}  trigger={trigger}  spend=${spend:.4f}")

    elif etype == "error":
        print(f"  {_C.CYAN}{i+1:>3}.{_C.RESET} {_C.BRED}ERROR{_C.RESET}  {event.get('error_message', '?')}")


def _render_watch_all(traces: list[dict]) -> None:
    """Render summary table of all traces."""
    print(f"{_C.BCYAN}[wickd watch]{_C.RESET} {len(traces)} trace(s) | {datetime.now().strftime('%H:%M:%S')}\n")
    print(f"{_C.BOLD}{'ID':<10} {'Agent':<20} {'Status':<18} {'Cost':>10}  {'Calls':>5}  {'Duration':>10}{_C.RESET}")
    print(f"{_C.DIM}{'-' * 80}{_C.RESET}")

    for t in traces:
        trace_id = t.get("trace_id", "?")[:8]
        agent_name = t.get("agent_name", "?")[:18]
        status = format_status(t.get("status", "?"))
        cost_val = t.get("total_cost", 0)
        cost_raw = format_cost_raw(cost_val)
        calls = t.get("llm_calls", 0)
        duration_str = format_duration(t.get("duration_ms"))

        cost_padding = 10 - len(cost_raw)
        cost_display = " " * max(cost_padding, 0) + format_cost(cost_val)

        print(f"{trace_id:<10} {agent_name:<20} {status:<28} {cost_display}  {calls:>5}  {duration_str:>10}")

    print(f"\n{_C.DIM}Ctrl+C to exit{_C.RESET}")


def cmd_watch(args: object) -> None:
    """Live-tail agent traces as they run."""
    store = TraceStore()
    agent_filter = args.agent
    show_all = args.all

    print(f"\n{_C.BCYAN}[wickd watch]{_C.RESET} Live-tailing traces...")
    print(f"  Directory: {store.trace_dir}")
    if agent_filter:
        print(f"  Filter: agent={agent_filter}")
    print(f"  Press Ctrl+C to stop.\n")

    seen_mtimes: dict[str, float] = {}

    try:
        while True:
            trace_files = sorted(store.trace_dir.glob("*.json"), key=os.path.getmtime, reverse=True)

            if not trace_files:
                time.sleep(0.5)
                continue

            current_mtimes: dict[str, float] = {}
            changed = False
            for f in trace_files:
                try:
                    mtime = os.path.getmtime(f)
                    current_mtimes[str(f)] = mtime
                    if str(f) not in seen_mtimes or seen_mtimes[str(f)] != mtime:
                        changed = True
                except OSError:
                    continue

            if set(seen_mtimes.keys()) != set(current_mtimes.keys()):
                changed = True

            if not changed:
                time.sleep(0.5)
                continue

            seen_mtimes = current_mtimes

            traces = []
            for f in trace_files:
                try:
                    t = json.loads(f.read_text())
                    if agent_filter and t.get("agent_name") != agent_filter:
                        continue
                    traces.append(t)
                except (json.JSONDecodeError, OSError):
                    continue

            if not traces:
                time.sleep(0.5)
                continue

            print("\033[2J\033[H", end="", flush=True)

            if show_all:
                _render_watch_all(traces)
            else:
                _render_watch_trace(traces[0])

            time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n{_C.BCYAN}[wickd watch]{_C.RESET} Stopped.")
