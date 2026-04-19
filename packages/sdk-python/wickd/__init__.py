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
from wickd.trace import Trace, TraceStore
from wickd.interceptor import patch_openai, patch_anthropic, patch_google, patch_all, patch_status, verify_patches, status
from wickd.tools import track_tool, tool_approval, record_tool_call
from wickd.mcp_patch import (
    patch_mcp,
    verify_mcp_patch,
    mcp_patch_status,
    set_mcp_approval_config,
    reset_mcp_approval_config,
    unpatch_mcp,
)
from wickd import notify
from wickd.notify import slack_interactive
from wickd import pricing

__version__ = "0.5.0"

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
    "patch_mcp",
    "verify_mcp_patch",
    "mcp_patch_status",
    "set_mcp_approval_config",
    "reset_mcp_approval_config",
    "unpatch_mcp",
    "notify",
    "slack_interactive",
    "pricing",
    "__version__",
]
