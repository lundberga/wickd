from wickd.budget import Budget, BudgetExceeded, WickdPatchError
from wickd.approvals import (
    approval,
    ApprovalRequired,
    ApprovalDenied,
    webhook_approval_handler,
    file_approval_handler,
    auto_approve_handler,
    auto_deny_handler,
)
from wickd.agent import agent
from wickd.trace import Trace, TraceStore, CloudTraceSync
from wickd.interceptor import patch_openai, patch_anthropic, patch_google, patch_all, patch_status, verify_patches, status
from wickd.tools import track_tool, tool_approval, record_tool_call
from wickd import notify
from wickd.notify import slack_interactive

__version__ = "0.1.0"

__all__ = [
    "agent",
    "Budget",
    "BudgetExceeded",
    "WickdPatchError",
    "approval",
    "ApprovalRequired",
    "ApprovalDenied",
    "webhook_approval_handler",
    "file_approval_handler",
    "auto_approve_handler",
    "auto_deny_handler",
    "Trace",
    "TraceStore",
    "CloudTraceSync",
    "patch_openai",
    "patch_anthropic",
    "patch_google",
    "patch_all",
    "patch_status",
    "verify_patches",
    "status",
    "track_tool",
    "tool_approval",
    "record_tool_call",
    "notify",
    "slack_interactive",
    "__version__",
]
