import json
import urllib.request
import urllib.error
from typing import Optional, Callable


def console(prefix: str = "[wickd]") -> Callable:
    """Print notifications to stdout."""

    def handler(event: dict):
        etype = event.get("type", "unknown")
        if etype == "budget_kill":
            print(f"{prefix} BUDGET KILLED -- ${event.get('spend', 0):.4f} spent. "
                  f"Trigger: {event.get('trigger', 'unknown')}")
        elif etype == "approval_request":
            print(f"{prefix} APPROVAL NEEDED -- {event.get('name', 'unknown')} "
                  f"(run: {event.get('trace_id', 'unknown')[:8]})")
        elif etype == "run_complete":
            summary = event.get("summary", {})
            print(f"{prefix} Run complete -- ${summary.get('run_spend', 0):.4f} spent, "
                  f"{summary.get('call_count', 0)} LLM calls")
        else:
            print(f"{prefix} {event}")

    return handler


def slack(webhook_url: str) -> Callable:
    """Send notifications to Slack via incoming webhook."""

    def handler(event: dict):
        etype = event.get("type", "unknown")

        if etype == "budget_kill":
            text = (
                f":rotating_light: *Wickd Budget Kill*\n"
                f"Agent spent *${event.get('spend', 0):.4f}* and hit the "
                f"`{event.get('trigger', 'unknown')}` cap.\n"
                f"Trace: `{event.get('trace_id', 'unknown')[:8]}`"
            )
        elif etype == "approval_request":
            text = (
                f":raised_hand: *Wickd Approval Needed*\n"
                f"Action `{event.get('name', 'unknown')}` requires approval.\n"
                f"Agent: `{event.get('agent_name', 'unknown')}` | "
                f"Trace: `{event.get('trace_id', 'unknown')[:8]}`"
            )
        elif etype == "run_complete":
            summary = event.get("summary", {})
            text = (
                f":white_check_mark: *Agent Run Complete*\n"
                f"Cost: *${summary.get('run_spend', 0):.4f}* | "
                f"Calls: {summary.get('call_count', 0)} | "
                f"Status: {event.get('status', 'unknown')}"
            )
        else:
            text = f"Wickd event: {json.dumps(event)}"

        if webhook_url.startswith("http"):
            try:
                payload = json.dumps({"text": text}).encode("utf-8")
                req = urllib.request.Request(
                    webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
            except (urllib.error.URLError, OSError):
                pass

    return handler


def slack_interactive(webhook_url: str) -> Callable:
    """Send Slack Block Kit messages with interactive approve/deny buttons.

    Approval requests include clickable buttons that post back to a
    Slack interaction endpoint.  Other event types fall back to simple
    text messages identical to :func:`slack`.
    """

    def handler(event: dict):
        etype = event.get("type", "unknown")

        if etype == "approval_request":
            trace_id = event.get("trace_id", "unknown")
            action_name = event.get("name", "unknown")
            agent_name = event.get("agent_name", "unknown")
            args_preview = event.get("args_preview", "")
            timestamp = event.get("timestamp", "")

            action_value = json.dumps({
                "trace_id": trace_id,
                "approval_name": action_name,
            })

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "\U0001f512 Approval Required: " + action_name,
                        "emoji": True,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                f"*Agent:* `{agent_name}` | "
                                f"*Trace:* `{trace_id[:8]}` | "
                                f"*Time:* {timestamp or 'now'}"
                            ),
                        }
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"The agent wants to execute *`{action_name}`*."
                            + (f"\n```{args_preview[:500]}```" if args_preview else "")
                        ),
                    },
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "\u2705 Approve",
                                "emoji": True,
                            },
                            "style": "primary",
                            "action_id": "wickd_approve",
                            "value": action_value,
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "\u274c Deny",
                                "emoji": True,
                            },
                            "style": "danger",
                            "action_id": "wickd_deny",
                            "value": action_value,
                        },
                    ],
                },
            ]

            payload = json.dumps({"blocks": blocks}).encode("utf-8")
        elif etype == "budget_kill":
            text = (
                f":rotating_light: *Wickd Budget Kill*\n"
                f"Agent spent *${event.get('spend', 0):.4f}* and hit the "
                f"`{event.get('trigger', 'unknown')}` cap.\n"
                f"Trace: `{event.get('trace_id', 'unknown')[:8]}`"
            )
            payload = json.dumps({"text": text}).encode("utf-8")
        elif etype == "run_complete":
            summary = event.get("summary", {})
            text = (
                f":white_check_mark: *Agent Run Complete*\n"
                f"Cost: *${summary.get('run_spend', 0):.4f}* | "
                f"Calls: {summary.get('call_count', 0)} | "
                f"Status: {event.get('status', 'unknown')}"
            )
            payload = json.dumps({"text": text}).encode("utf-8")
        else:
            payload = json.dumps({"text": f"Wickd event: {json.dumps(event)}"}).encode("utf-8")

        if webhook_url.startswith("http"):
            try:
                req = urllib.request.Request(
                    webhook_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
            except (urllib.error.URLError, OSError):
                pass

    return handler


def webhook(url: str, headers: Optional[dict] = None) -> Callable:
    """POST event JSON to any webhook URL."""

    def handler(event: dict):
        try:
            payload = json.dumps(event).encode("utf-8")
            req_headers = {"Content-Type": "application/json"}
            if headers:
                req_headers.update(headers)
            req = urllib.request.Request(url, data=payload, headers=req_headers)
            urllib.request.urlopen(req, timeout=5)
        except (urllib.error.URLError, OSError):
            pass

    return handler
