"""wickd status and version subcommands."""

from pathlib import Path
from wickd.trace import TraceStore
from wickd.cli_render import _C, format_cost, format_status, parse_time


def cmd_status(args: object) -> None:
    store = TraceStore()
    traces = store.list_traces(limit=50)

    if not traces:
        print("No agent runs found.")
        return

    agents: dict[str, dict] = {}
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
        cost = format_cost(a["total_cost"])
        last_time = parse_time(a["last_run"] or "")
        status = format_status(a["last_status"] or "?")
        print(f"{name:<22} {a['runs']:<7} {cost:<24} {a['kills']:<7} {a['errors']:<8} {last_time:<18} {status}")

    approval_dir = Path.home() / ".wickd" / "approvals"
    if approval_dir.exists():
        pending = list(approval_dir.glob("*.pending"))
        if pending:
            print(f"\n{_C.BYELLOW}  {len(pending)} pending approval(s) -- run 'wickd approve' to review{_C.RESET}")

    print(f"\n{len(traces)} recent runs across {len(agents)} agent(s) | wickd traces for details\n")


def cmd_version(args: object) -> None:
    from wickd import __version__
    print(f"wickd {__version__}")
