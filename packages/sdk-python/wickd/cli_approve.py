"""wickd approve subcommand."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from wickd.cli_render import _C


def cmd_approve(args: object) -> None:
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

                _print_approval_request(data)
                request_id = pending_file.stem

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


def _print_approval_request(data: dict) -> None:
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
