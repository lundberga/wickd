"""wickd traces subcommand."""

import sys
from wickd.trace import TraceStore
from wickd.cli_render import (
    _C, format_cost, format_cost_raw, format_status, format_duration, parse_time, progress_bar
)


def _print_trace_detail(trace: dict) -> None:
    print(f"\n{_C.BOLD}{'=' * 60}{_C.RESET}")
    print(f"  {_C.BCYAN}WICKD TRACE: {trace.get('trace_id', '?')[:8]}{_C.RESET}")
    print(f"{_C.BOLD}{'=' * 60}{_C.RESET}")
    print(f"  Agent:    {trace.get('agent_name', '?')}")
    print(f"  Task:     {trace.get('task', '--')}")
    print(f"  Status:   {format_status(trace.get('status', '?'))}")
    print(f"  Cost:     {format_cost(trace.get('total_cost', 0))}")
    print(f"  Tokens:   {trace.get('total_input_tokens', 0):,} in / {trace.get('total_output_tokens', 0):,} out")
    print(f"  Calls:    {trace.get('llm_calls', 0)}")
    print(f"  Duration: {format_duration(trace.get('duration_ms'))}")
    print(f"  Started:  {trace.get('started_at', '--')}")

    budget = trace.get("budget_summary")
    if budget:
        caps = budget.get("caps", {})
        print(f"\n  {_C.BOLD}Budget:{_C.RESET}")
        if caps.get("per_run"):
            spend = budget.get("run_spend", 0)
            cap = caps["per_run"]
            print(f"    Per-run: ${spend:.4f} / ${cap:.2f}  {progress_bar(spend, cap)}")
        if caps.get("daily"):
            spend = budget.get("daily_spend", 0)
            cap = caps["daily"]
            print(f"    Daily:   ${spend:.4f} / ${cap:.2f}  {progress_bar(spend, cap)}")

    events = trace.get("events", [])
    if events:
        print(f"\n  {_C.DIM}{'-' * 56}{_C.RESET}")
        print(f"  {_C.BOLD}EVENTS ({len(events)}):{_C.RESET}")
        print(f"  {_C.DIM}{'-' * 56}{_C.RESET}")
        for i, event in enumerate(events):
            _print_event(i, event)

    print(f"\n{_C.BOLD}{'=' * 60}{_C.RESET}\n")


def _print_event(i: int, event: dict) -> None:
    etype = event.get("event_type", "?")

    if etype == "llm_call":
        model = event.get("model", "?")
        cost = event.get("cost", 0)
        tokens_in = event.get("input_tokens", 0)
        tokens_out = event.get("output_tokens", 0)
        latency = event.get("latency_ms", 0)
        cumulative = event.get("spend_at_event", 0)
        print(f"\n  {_C.CYAN}[{i+1}]{_C.RESET} {_C.BOLD}LLM Call{_C.RESET} -- {model}")
        print(f"      Cost: {format_cost(cost)} (cumulative: ${cumulative:.4f})")
        print(f"      Tokens: {tokens_in:,} in / {tokens_out:,} out | {format_duration(latency)}")
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


def cmd_traces(args: object) -> None:
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

    print(f"\n{_C.BOLD}{'ID':<10} {'Agent':<20} {'Status':<18} {'Cost':>10}   {'Calls':>5}  {'Duration':>10}  {'Time'}{_C.RESET}")
    print(f"{_C.DIM}{'-' * 90}{_C.RESET}")

    total_cost = 0.0
    total_calls = 0

    for t in traces:
        trace_id = t.get("trace_id", "?")[:8]
        agent_name = t.get("agent_name", "?")[:18]
        status = format_status(t.get("status", "?"))
        cost_val = t.get("total_cost", 0)
        cost_raw = format_cost_raw(cost_val)
        calls = t.get("llm_calls", 0)
        duration_str = format_duration(t.get("duration_ms"))
        time_str = parse_time(t.get("started_at", ""))

        total_cost += cost_val
        total_calls += calls

        cost_padding = 10 - len(cost_raw)
        cost_display = " " * max(cost_padding, 0) + format_cost(cost_val)

        print(f"{trace_id:<10} {agent_name:<20} {status:<28} {cost_display}   {calls:>5}  {duration_str:>10}  {time_str}")

    print(f"{_C.DIM}{'-' * 90}{_C.RESET}")
    print(
        f"{_C.CYAN}{len(traces)} trace(s){_C.RESET} | "
        f"{_C.CYAN}${total_cost:.4f} total cost{_C.RESET} | "
        f"{_C.CYAN}{total_calls} LLM call(s){_C.RESET} | "
        f"wickd traces <id> for details\n"
    )
