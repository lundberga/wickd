import functools
import sys
import time
from typing import Optional, Callable, Any
from wickd.interceptor import get_active_trace


class ApprovalRequired(Exception):
    """Raised when an action requires approval and none is available."""

    def __init__(self, name: str, args: tuple = (), kwargs: dict = None):
        self.name = name
        self.args = args
        self.kwargs = kwargs or {}
        super().__init__(f"Approval required for '{name}'.")


class ApprovalDenied(Exception):
    """Raised when a human denies an approval request."""

    def __init__(self, name: str, reason: str = ""):
        self.name = name
        self.reason = reason
        super().__init__(f"Approval denied for '{name}'. {reason}".strip())


class ApprovalGate:
    """Wraps a function with a human approval checkpoint."""

    def __init__(
        self,
        fn: Callable,
        name: str,
        handler: Optional[Callable] = None,
        timeout: float = 300,
    ):
        self.fn = fn
        self.name = name
        self.handler = handler or _terminal_handler
        self.timeout = timeout
        functools.update_wrapper(self, fn)

    def __call__(self, *args, **kwargs) -> Any:
        trace = get_active_trace()

        if trace:
            trace.add_approval(self.name, "pending")

        context = {
            "name": self.name,
            "function": self.fn.__name__,
            "args_preview": str(args)[:200] if args else "",
            "kwargs_preview": str(kwargs)[:200] if kwargs else "",
            "trace_id": trace.trace_id[:8] if trace else "unknown",
        }

        print(
            f"\033[33m[wickd] APPROVAL NEEDED: '{self.name}'\033[0m",
            file=sys.stderr,
        )

        approved = self.handler(context)

        if approved:
            if trace:
                trace.add_approval(self.name, "approved")
            print(f"\033[32m[wickd] Approved: '{self.name}'\033[0m", file=sys.stderr)
            return self.fn(*args, **kwargs)

        if trace:
            trace.add_approval(self.name, "denied")
        print(f"\033[31m[wickd] Denied: '{self.name}'\033[0m", file=sys.stderr)
        raise ApprovalDenied(self.name)


def _terminal_handler(context: dict) -> bool:
    """Default handler -- prompts in the terminal."""
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"  WICKD APPROVAL REQUEST", file=sys.stderr)
    print(f"  Action: {context['name']}", file=sys.stderr)
    print(f"  Function: {context['function']}", file=sys.stderr)
    if context.get("args_preview"):
        print(f"  Args: {context['args_preview']}", file=sys.stderr)
    if context.get("kwargs_preview"):
        print(f"  Kwargs: {context['kwargs_preview']}", file=sys.stderr)
    print(f"{'='*50}", file=sys.stderr)

    try:
        response = input("  Approve? [y/N]: ").strip().lower()
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def slack_approval_handler(webhook_url: str) -> Callable:
    """Send approval request to Slack, then fall back to terminal for response."""
    import json
    import urllib.request

    def handler(context: dict) -> bool:
        text = (
            f":raised_hand: *Approval Needed*\n"
            f"Action: `{context['name']}`\n"
            f"Function: `{context['function']}`\n"
            f"Trace: `{context.get('trace_id', 'unknown')}`\n"
            f"_Respond in terminal to approve/deny._"
        )

        if webhook_url.startswith("http"):
            try:
                payload = json.dumps({"text": text}).encode("utf-8")
                req = urllib.request.Request(
                    webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass

        return _terminal_handler(context)

    return handler


def webhook_approval_handler(
    url: str,
    *,
    poll_interval: float = 2.0,
    timeout: float = 300,
    headers: Optional[dict] = None,
) -> Callable:
    """
    POST approval requests to a webhook, poll for decisions.

    The endpoint should return either:
        {"approved": true/false}           -- instant decision
        {"poll_url": "...", "request_id": "..."}  -- deferred, poll for result

    Poll URL returns: {"status": "pending|approved|denied"}
    """
    import json
    import urllib.request
    import urllib.error

    def handler(context: dict) -> bool:
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        payload = json.dumps({
            "type": "approval_request",
            "name": context["name"],
            "function": context["function"],
            "args_preview": context.get("args_preview", ""),
            "kwargs_preview": context.get("kwargs_preview", ""),
            "trace_id": context.get("trace_id", "unknown"),
        }).encode("utf-8")

        try:
            req = urllib.request.Request(url, data=payload, headers=req_headers)
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(
                f"\033[31m[wickd] Webhook approval failed: {e}. Falling back to terminal.\033[0m",
                file=sys.stderr,
            )
            return _terminal_handler(context)

        if "approved" in data:
            return bool(data["approved"])

        poll_url = data.get("poll_url")
        if not poll_url:
            print(
                "\033[31m[wickd] Webhook returned no 'approved' or 'poll_url'. Falling back to terminal.\033[0m",
                file=sys.stderr,
            )
            return _terminal_handler(context)

        request_id = data.get("request_id", "?")
        print(
            f"\033[33m[wickd] Waiting for remote approval (request: {request_id})...\033[0m",
            file=sys.stderr,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                poll_req = urllib.request.Request(poll_url, headers=req_headers)
                poll_resp = urllib.request.urlopen(poll_req, timeout=10)
                poll_data = json.loads(poll_resp.read().decode("utf-8"))
            except Exception:
                continue

            status = poll_data.get("status", "pending")
            if status == "approved":
                return True
            elif status == "denied":
                return False

        print(
            f"\033[31m[wickd] Approval timed out after {timeout}s. Denying.\033[0m",
            file=sys.stderr,
        )
        return False

    return handler


def file_approval_handler(
    approval_dir: Optional[str] = None,
    poll_interval: float = 1.0,
    timeout: float = 300,
) -> Callable:
    """
    File-based approval for headless/CI use.

    Writes a .pending file; another process renames it to .approved or .denied.
    """
    from pathlib import Path
    import json

    base_dir = Path(approval_dir) if approval_dir else Path.home() / ".wickd" / "approvals"

    def handler(context: dict) -> bool:
        base_dir.mkdir(parents=True, exist_ok=True)

        request_id = f"{context.get('trace_id', 'unknown')}_{context['name']}_{int(time.time())}"
        pending_file = base_dir / f"{request_id}.pending"
        approved_file = base_dir / f"{request_id}.approved"
        denied_file = base_dir / f"{request_id}.denied"

        pending_file.write_text(json.dumps({
            "name": context["name"],
            "function": context["function"],
            "args_preview": context.get("args_preview", ""),
            "kwargs_preview": context.get("kwargs_preview", ""),
            "trace_id": context.get("trace_id", "unknown"),
            "requested_at": time.time(),
        }, indent=2))

        print(f"\033[33m[wickd] Approval request written to: {pending_file}\033[0m", file=sys.stderr)
        print(f"\033[33m[wickd] Waiting for approval...\033[0m", file=sys.stderr)

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(poll_interval)

            if approved_file.exists():
                approved_file.unlink(missing_ok=True)
                pending_file.unlink(missing_ok=True)
                return True
            elif denied_file.exists():
                denied_file.unlink(missing_ok=True)
                pending_file.unlink(missing_ok=True)
                return False
            elif not pending_file.exists():
                return False

        pending_file.unlink(missing_ok=True)
        print(
            f"\033[31m[wickd] Approval timed out after {timeout}s. Denying.\033[0m",
            file=sys.stderr,
        )
        return False

    return handler


def auto_approve_handler(context: dict) -> bool:
    """Auto-approve. Testing only."""
    return True


def auto_deny_handler(context: dict) -> bool:
    """Auto-deny. Testing only."""
    return False


def approval(
    name: str,
    *,
    handler: Optional[Callable] = None,
    timeout: float = 300,
) -> Callable:
    """Decorator to gate a function behind human approval."""

    def decorator(fn: Callable) -> ApprovalGate:
        return ApprovalGate(fn=fn, name=name, handler=handler, timeout=timeout)

    return decorator
