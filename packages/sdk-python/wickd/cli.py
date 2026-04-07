import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from wickd.trace import TraceStore


# ── ANSI helpers ──────────────────────────────────────────────────────────────

class _C:
    """ANSI colour/style constants."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    CYAN    = "\033[36m"
    BRED    = "\033[1;31m"
    BGREEN  = "\033[1;32m"
    BYELLOW = "\033[1;33m"
    BCYAN   = "\033[1;36m"


def _format_cost(cost: float) -> str:
    if cost >= 1.0:
        return f"{_C.RED}${cost:.4f}{_C.RESET}"
    elif cost >= 0.10:
        return f"{_C.YELLOW}${cost:.4f}{_C.RESET}"
    return f"{_C.GREEN}${cost:.4f}{_C.RESET}"


def _format_cost_raw(cost: float) -> str:
    """Return cost string without ANSI (for width calculations)."""
    return f"${cost:.4f}"


STATUS_ICONS = {
    "completed":     f"{_C.BGREEN}\u2713 completed{_C.RESET}",
    "budget_killed": f"{_C.BRED}\u2717 KILLED{_C.RESET}",
    "error":         f"{_C.BRED}\u2717 ERROR{_C.RESET}",
    "running":       f"{_C.BYELLOW}\u21bb running{_C.RESET}",
}


def _format_status(status: str) -> str:
    return STATUS_ICONS.get(status, status)


def _format_duration(ms) -> str:
    """Human-friendly duration from milliseconds."""
    if ms is None:
        return "--"
    ms = float(ms)
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = int(secs // 60)
    remainder = secs - mins * 60
    return f"{mins}m {remainder:.0f}s"


def _parse_time(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M")
    except (ValueError, AttributeError):
        return "--"


def _progress_bar(current: float, cap: float, width: int = 20) -> str:
    """Render a text progress bar with colour."""
    if cap <= 0:
        return ""
    ratio = min(current / cap, 1.0)
    filled = int(round(ratio * width))
    empty = width - filled
    if ratio >= 1.0:
        colour = _C.RED
    elif ratio >= 0.75:
        colour = _C.YELLOW
    else:
        colour = _C.GREEN
    bar = f"{colour}{'█' * filled}{'░' * empty}{_C.RESET}"
    pct = f"{ratio * 100:.0f}%"
    return f"[{bar}] {pct}"


# ── wickd traces ──────────────────────────────────────────────────────────────

def cmd_traces(args):
    store = TraceStore()

    if args.trace_id:
        trace = store.get_trace(args.trace_id)
        if not trace:
            print(f"Trace '{args.trace_id}' not found.", file=sys.stderr)
            sys.exit(1)
        _print_trace_detail(trace)
        return

    traces = store.list_traces(limit=args.limit or 20)

    if not traces:
        print("No traces found. Run an agent with Wickd to create traces.")
        print(f"Trace directory: {store.trace_dir}")
        return

    if args.cost:
        traces.sort(key=lambda t: t.get("total_cost", 0), reverse=True)

    # Header
    print(f"\n{_C.BOLD}{'ID':<10} {'Agent':<20} {'Status':<18} {'Cost':>10}   {'Calls':>5}  {'Duration':>10}  {'Time'}{_C.RESET}")
    print(f"{_C.DIM}{'-' * 90}{_C.RESET}")

    total_cost = 0.0
    total_calls = 0

    for t in traces:
        trace_id = t.get("trace_id", "?")[:8]
        agent_name = t.get("agent_name", "?")[:18]
        status = _format_status(t.get("status", "?"))
        cost_val = t.get("total_cost", 0)
        cost = _format_cost(cost_val)
        cost_raw = _format_cost_raw(cost_val)
        calls = t.get("llm_calls", 0)
        duration_str = _format_duration(t.get("duration_ms"))
        time_str = _parse_time(t.get("started_at", ""))

        total_cost += cost_val
        total_calls += calls

        # Right-align cost: pad based on raw width, then use coloured string
        cost_padding = 10 - len(cost_raw)
        cost_display = " " * max(cost_padding, 0) + cost

        print(f"{trace_id:<10} {agent_name:<20} {status:<28} {cost_display}   {calls:>5}  {duration_str:>10}  {time_str}")

    # Summary line
    print(f"{_C.DIM}{'-' * 90}{_C.RESET}")
    print(
        f"{_C.CYAN}{len(traces)} trace(s){_C.RESET} | "
        f"{_C.CYAN}${total_cost:.4f} total cost{_C.RESET} | "
        f"{_C.CYAN}{total_calls} LLM call(s){_C.RESET} | "
        f"wickd traces <id> for details\n"
    )


def _print_trace_detail(trace: dict):
    print(f"\n{_C.BOLD}{'=' * 60}{_C.RESET}")
    print(f"  {_C.BCYAN}WICKD TRACE: {trace.get('trace_id', '?')[:8]}{_C.RESET}")
    print(f"{_C.BOLD}{'=' * 60}{_C.RESET}")
    print(f"  Agent:    {trace.get('agent_name', '?')}")
    print(f"  Task:     {trace.get('task', '--')}")
    print(f"  Status:   {_format_status(trace.get('status', '?'))}")
    print(f"  Cost:     {_format_cost(trace.get('total_cost', 0))}")
    print(f"  Tokens:   {trace.get('total_input_tokens', 0):,} in / {trace.get('total_output_tokens', 0):,} out")
    print(f"  Calls:    {trace.get('llm_calls', 0)}")
    duration = trace.get("duration_ms")
    print(f"  Duration: {_format_duration(duration)}")
    print(f"  Started:  {trace.get('started_at', '--')}")

    budget = trace.get("budget_summary")
    if budget:
        caps = budget.get("caps", {})
        print(f"\n  {_C.BOLD}Budget:{_C.RESET}")
        if caps.get("per_run"):
            spend = budget.get("run_spend", 0)
            cap = caps["per_run"]
            bar = _progress_bar(spend, cap)
            print(f"    Per-run: ${spend:.4f} / ${cap:.2f}  {bar}")
        if caps.get("daily"):
            spend = budget.get("daily_spend", 0)
            cap = caps["daily"]
            bar = _progress_bar(spend, cap)
            print(f"    Daily:   ${spend:.4f} / ${cap:.2f}  {bar}")

    events = trace.get("events", [])
    if events:
        print(f"\n  {_C.DIM}{'-' * 56}{_C.RESET}")
        print(f"  {_C.BOLD}EVENTS ({len(events)}):{_C.RESET}")
        print(f"  {_C.DIM}{'-' * 56}{_C.RESET}")

        for i, event in enumerate(events):
            etype = event.get("event_type", "?")

            if etype == "llm_call":
                model = event.get("model", "?")
                cost = event.get("cost", 0)
                tokens_in = event.get("input_tokens", 0)
                tokens_out = event.get("output_tokens", 0)
                latency = event.get("latency_ms", 0)
                cumulative = event.get("spend_at_event", 0)

                print(f"\n  {_C.CYAN}[{i+1}]{_C.RESET} {_C.BOLD}LLM Call{_C.RESET} -- {model}")
                print(f"      Cost: {_format_cost(cost)} (cumulative: ${cumulative:.4f})")
                print(f"      Tokens: {tokens_in:,} in / {tokens_out:,} out | {_format_duration(latency)}")

                if event.get("prompt_preview"):
                    print(f"      Prompt: \"{event['prompt_preview'][:80]}...\"")
                if event.get("response_preview"):
                    print(f"      Response: \"{event['response_preview'][:80]}...\"")

            elif etype == "approval_gate":
                name = event.get("approval_name", "?")
                status = event.get("approval_status", "?")
                if status == "approved":
                    icon = f"{_C.GREEN}Y{_C.RESET}"
                elif status == "denied":
                    icon = f"{_C.RED}X{_C.RESET}"
                else:
                    icon = f"{_C.YELLOW}?{_C.RESET}"
                print(f"\n  {_C.CYAN}[{i+1}]{_C.RESET} {_C.BOLD}Approval Gate{_C.RESET} -- {name} [{icon} {status}]")

            elif etype == "budget_kill":
                trigger = event.get("budget_trigger", "?")
                spend = event.get("spend_at_event", 0)
                print(f"\n  {_C.CYAN}[{i+1}]{_C.RESET} {_C.BRED}BUDGET KILL{_C.RESET} -- trigger: {trigger}, spend: ${spend:.4f}")

            elif etype == "error":
                print(f"\n  {_C.CYAN}[{i+1}]{_C.RESET} {_C.BRED}ERROR{_C.RESET} -- {event.get('error_message', '?')}")

    print(f"\n{_C.BOLD}{'=' * 60}{_C.RESET}\n")


# ── wickd approve ─────────────────────────────────────────────────────────────

def cmd_approve(args):
    approval_dir = Path(args.dir) if args.dir else Path.home() / ".wickd" / "approvals"
    approval_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{_C.BYELLOW}[wickd approve]{_C.RESET} Watching for approval requests...")
    print(f"  Directory: {approval_dir}")
    print(f"  Press Ctrl+C to stop.\n")

    try:
        while True:
            pending_files = sorted(approval_dir.glob("*.pending"), key=os.path.getmtime)

            if not pending_files:
                time.sleep(0.5)
                continue

            for pending_file in pending_files:
                try:
                    data = json.loads(pending_file.read_text())
                except (json.JSONDecodeError, OSError):
                    continue

                request_id = pending_file.stem

                print(f"{_C.YELLOW}{'=' * 50}{_C.RESET}")
                print(f"  {_C.BYELLOW}APPROVAL REQUEST{_C.RESET}")
                print(f"  Action:   {data.get('name', '?')}")
                print(f"  Function: {data.get('function', '?')}")
                if data.get("args_preview"):
                    print(f"  Args:     {data['args_preview']}")
                if data.get("kwargs_preview"):
                    print(f"  Kwargs:   {data['kwargs_preview']}")
                print(f"  Trace:    {data.get('trace_id', '?')}")
                requested_at = data.get("requested_at")
                if requested_at:
                    try:
                        dt = datetime.fromtimestamp(requested_at)
                        age = time.time() - requested_at
                        print(f"  Time:     {dt.strftime('%H:%M:%S')} ({age:.0f}s ago)")
                    except (ValueError, OSError):
                        pass
                print(f"{_C.YELLOW}{'=' * 50}{_C.RESET}")

                try:
                    response = input("  Approve? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\n")
                    return

                if response in ("y", "yes"):
                    pending_file.rename(approval_dir / f"{request_id}.approved")
                    print(f"  {_C.BGREEN}Approved{_C.RESET}\n")
                else:
                    pending_file.rename(approval_dir / f"{request_id}.denied")
                    print(f"  {_C.BRED}Denied{_C.RESET}\n")

    except KeyboardInterrupt:
        print(f"\n{_C.BYELLOW}[wickd approve]{_C.RESET} Stopped.")


# ── wickd status ──────────────────────────────────────────────────────────────

def cmd_status(args):
    store = TraceStore()
    traces = store.list_traces(limit=50)

    if not traces:
        print("No agent runs found.")
        return

    agents = {}
    for t in traces:
        name = t.get("agent_name", "unknown")
        if name not in agents:
            agents[name] = {
                "runs": 0, "total_cost": 0.0, "kills": 0, "errors": 0,
                "last_run": None, "last_status": None,
            }
        a = agents[name]
        a["runs"] += 1
        a["total_cost"] += t.get("total_cost", 0)
        status = t.get("status")
        if status == "budget_killed":
            a["kills"] += 1
        elif status == "error":
            a["errors"] += 1
        if a["last_run"] is None:
            a["last_run"] = t.get("started_at", "")
            a["last_status"] = status

    print(f"\n{_C.BOLD}{'Agent':<22} {'Runs':<7} {'Cost':<14} {'Kills':<7} {'Errors':<8} {'Last Run':<18} {'Status'}{_C.RESET}")
    print(f"{_C.DIM}{'-' * 95}{_C.RESET}")

    for name, a in sorted(agents.items()):
        cost = _format_cost(a["total_cost"])
        last_time = _parse_time(a["last_run"] or "")
        status = _format_status(a["last_status"] or "?")
        print(f"{name:<22} {a['runs']:<7} {cost:<24} {a['kills']:<7} {a['errors']:<8} {last_time:<18} {status}")

    approval_dir = Path.home() / ".wickd" / "approvals"
    if approval_dir.exists():
        pending = list(approval_dir.glob("*.pending"))
        if pending:
            print(f"\n{_C.BYELLOW}  {len(pending)} pending approval(s) -- run 'wickd approve' to review{_C.RESET}")

    print(f"\n{len(traces)} recent runs across {len(agents)} agent(s) | wickd traces for details\n")


# ── wickd init ────────────────────────────────────────────────────────────────

def cmd_init(args):
    """Scaffold a Wickd agent and config in the current directory."""
    agent_file = Path("wickd_agent.py")
    config_file = Path("wickd.json")
    created = []

    if not agent_file.exists():
        agent_file.write_text('''import wickd


@wickd.approval("database_write")
def update_record(record_id: str, data: dict):
    """Protected action -- requires approval."""
    print(f"  Updated record {record_id}")
    return {"updated": True}


@wickd.agent(
    budget=wickd.Budget(per_run=2.00, daily=20.00),
    on_budget_kill=wickd.notify.console(),
)
def my_agent(task: str):
    # Wickd intercepts OpenAI/Anthropic/Google SDK calls automatically.
    # Just use them normally:
    #
    # import openai
    # client = openai.OpenAI()
    # response = client.chat.completions.create(
    #     model="gpt-4o",
    #     messages=[{"role": "user", "content": task}],
    # )

    print(f"Agent running: {task}")
    return "done"


if __name__ == "__main__":
    import sys
    task = " ".join(sys.argv[1:]) or "Hello from Wickd"
    try:
        result = my_agent.run(task)
        print(f"Result: {result}")
    except wickd.BudgetExceeded as e:
        print(f"Budget killed: {e}")
    except wickd.ApprovalDenied as e:
        print(f"Approval denied: {e}")
''')
        created.append(str(agent_file))

    if not config_file.exists():
        config_file.write_text(json.dumps({
            "version": "0.1.0",
            "defaults": {
                "budget": {"per_run": 2.00, "daily": 20.00},
                "approvals": ["database_write", "send_email", "api_call_with_payment"],
                "notifications": {
                    "console": True,
                    "slack_webhook": "",
                },
            },
        }, indent=2) + "\n")
        created.append(str(config_file))

    if created:
        print(f"Created: {', '.join(created)}")
        print(f"\nNext steps:")
        print(f"  1. Edit {agent_file} with your agent logic")
        print(f"  2. Run: python {agent_file} \"Your task here\"")
        print(f"  3. View traces: wickd traces")
    else:
        print("Files already exist.", file=sys.stderr)


# ── wickd watch ───────────────────────────────────────────────────────────────

def _render_watch_trace(trace: dict):
    """Render a single trace for the watch view."""
    agent_name = trace.get("agent_name", "?")
    status = trace.get("status", "?")
    cost_val = trace.get("total_cost", 0)
    duration = trace.get("duration_ms")
    started = trace.get("started_at", "")

    # If still running, compute live duration
    if status == "running" and started:
        try:
            dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            elapsed_ms = (time.time() - dt.timestamp()) * 1000
            duration = elapsed_ms
        except (ValueError, AttributeError):
            pass

    # Header
    print(f"{_C.BOLD}{'=' * 64}{_C.RESET}")
    print(f"  {_C.BCYAN}WICKD WATCH{_C.RESET}  |  {_C.BOLD}{agent_name}{_C.RESET}  |  {_format_status(status)}  |  {_format_duration(duration)}")
    print(f"{_C.BOLD}{'=' * 64}{_C.RESET}")

    # Budget bar
    budget = trace.get("budget_summary")
    if budget:
        caps = budget.get("caps", {})
        if caps.get("per_run"):
            spend = budget.get("run_spend", cost_val)
            cap = caps["per_run"]
            bar = _progress_bar(spend, cap, width=30)
            print(f"\n  {_C.BOLD}Budget:{_C.RESET} ${spend:.4f} / ${cap:.2f}  {bar}")
    # Even without budget_summary, show cost
    print(f"\n  {_C.BOLD}Total cost:{_C.RESET} {_format_cost(cost_val)}    "
          f"{_C.BOLD}Tokens:{_C.RESET} {trace.get('total_input_tokens', 0):,} in / {trace.get('total_output_tokens', 0):,} out    "
          f"{_C.BOLD}Calls:{_C.RESET} {trace.get('llm_calls', 0)}")

    events = trace.get("events", [])
    if events:
        print(f"\n  {_C.DIM}{'-' * 60}{_C.RESET}")
        for i, event in enumerate(events):
            etype = event.get("event_type", "?")

            if etype == "llm_call":
                model = event.get("model", "?")
                cost = event.get("cost", 0)
                tokens_in = event.get("input_tokens", 0)
                tokens_out = event.get("output_tokens", 0)
                latency = event.get("latency_ms", 0)
                cumulative = event.get("spend_at_event", 0)

                print(f"  {_C.CYAN}{i+1:>3}.{_C.RESET} {_C.BOLD}LLM{_C.RESET} {model:<28} "
                      f"{_format_cost(cost):>20}  {tokens_in:>6,}+{tokens_out:<6,} tok  {_format_duration(latency):>8}")

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

    # Footer
    print(f"\n  {_C.DIM}Updated: {datetime.now().strftime('%H:%M:%S')} | Ctrl+C to exit{_C.RESET}")
    print(f"{_C.BOLD}{'=' * 64}{_C.RESET}")


def cmd_watch(args):
    """Live-tail agent traces as they run."""
    store = TraceStore()
    agent_filter = args.agent
    show_all = args.all

    print(f"\n{_C.BCYAN}[wickd watch]{_C.RESET} Live-tailing traces...")
    print(f"  Directory: {store.trace_dir}")
    if agent_filter:
        print(f"  Filter: agent={agent_filter}")
    print(f"  Press Ctrl+C to stop.\n")

    # Track file modification times to detect changes
    seen_mtimes: dict[str, float] = {}

    try:
        while True:
            # Scan for trace files
            trace_files = sorted(store.trace_dir.glob("*.json"), key=os.path.getmtime, reverse=True)

            if not trace_files:
                time.sleep(0.5)
                continue

            # Check if anything changed
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

            # Also detect deleted files
            if set(seen_mtimes.keys()) != set(current_mtimes.keys()):
                changed = True

            if not changed:
                time.sleep(0.5)
                continue

            seen_mtimes = current_mtimes

            # Load traces
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

            # Clear screen
            print("\033[2J\033[H", end="", flush=True)

            if show_all:
                # Show summary table of all traces
                print(f"{_C.BCYAN}[wickd watch]{_C.RESET} {len(traces)} trace(s) | {datetime.now().strftime('%H:%M:%S')}\n")
                print(f"{_C.BOLD}{'ID':<10} {'Agent':<20} {'Status':<18} {'Cost':>10}  {'Calls':>5}  {'Duration':>10}{_C.RESET}")
                print(f"{_C.DIM}{'-' * 80}{_C.RESET}")

                for t in traces:
                    trace_id = t.get("trace_id", "?")[:8]
                    agent_name = t.get("agent_name", "?")[:18]
                    status = _format_status(t.get("status", "?"))
                    cost_val = t.get("total_cost", 0)
                    cost = _format_cost(cost_val)
                    cost_raw = _format_cost_raw(cost_val)
                    calls = t.get("llm_calls", 0)
                    duration_str = _format_duration(t.get("duration_ms"))

                    cost_padding = 10 - len(cost_raw)
                    cost_display = " " * max(cost_padding, 0) + cost

                    print(f"{trace_id:<10} {agent_name:<20} {status:<28} {cost_display}  {calls:>5}  {duration_str:>10}")

                print(f"\n{_C.DIM}Ctrl+C to exit{_C.RESET}")
            else:
                # Show detail view of the most recent trace
                _render_watch_trace(traces[0])

            time.sleep(0.5)

    except KeyboardInterrupt:
        print(f"\n{_C.BCYAN}[wickd watch]{_C.RESET} Stopped.")


# ── wickd version ─────────────────────────────────────────────────────────────

def cmd_version(args):
    from wickd import __version__
    print(f"wickd {__version__}")


# ── main / argparse ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="wickd",
        description="Guardrails for AI agents.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # traces
    traces_parser = subparsers.add_parser("traces", help="View agent traces")
    traces_parser.add_argument("trace_id", nargs="?", help="Trace ID to view in detail")
    traces_parser.add_argument("--cost", action="store_true", help="Sort by cost (highest first)")
    traces_parser.add_argument("--limit", "-n", type=int, default=20, help="Max traces to show")
    traces_parser.set_defaults(func=cmd_traces)

    # approve
    approve_parser = subparsers.add_parser("approve", help="Review pending approval requests")
    approve_parser.add_argument("--dir", help="Approval directory")
    approve_parser.set_defaults(func=cmd_approve)

    # status
    status_parser = subparsers.add_parser("status", help="Agent status summary")
    status_parser.set_defaults(func=cmd_status)

    # init
    init_parser = subparsers.add_parser("init", help="Scaffold a Wickd agent")
    init_parser.set_defaults(func=cmd_init)

    # watch
    watch_parser = subparsers.add_parser("watch", help="Live-tail agent traces as they run")
    watch_parser.add_argument("--agent", help="Filter to a specific agent name")
    watch_parser.add_argument("--all", action="store_true", help="Show all traces (default: most recent)")
    watch_parser.set_defaults(func=cmd_watch)

    # version
    version_parser = subparsers.add_parser("version", help="Print version")
    version_parser.set_defaults(func=cmd_version)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    args.func(args)


if __name__ == "__main__":
    main()
