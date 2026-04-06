import functools
import os
import sys
import time
import warnings
from typing import Optional, Callable, Any

from wickd.budget import Budget, BudgetTracker, BudgetExceeded, WickdPatchError
from wickd.trace import Trace, TraceEvent, TraceStore, CloudTraceSync
from wickd.interceptor import (
    patch_all,
    verify_patches,
    set_active_tracker,
    set_active_trace,
)


class WickdAgent:
    """A wrapped agent function with budget enforcement, tracing, and notifications."""

    def __init__(
        self,
        fn: Callable,
        name: Optional[str] = None,
        budget: Optional[Budget] = None,
        approvals: Optional[list[str]] = None,
        on_budget_kill: Optional[Callable] = None,
        on_run_complete: Optional[Callable] = None,
        notify: Optional[list[Callable]] = None,
        trace_dir: Optional[str] = None,
        auto_patch: bool = True,
        on_patch_failure: str = "warn",
        cloud_endpoint: Optional[str] = None,
        cloud_api_key: Optional[str] = None,
    ):
        self.fn = fn
        self.name = name or fn.__name__
        self.budget = budget
        self.approvals = approvals or []
        self.on_budget_kill = on_budget_kill
        self.on_run_complete = on_run_complete
        self.notify_handlers = notify or []
        self.trace_store = TraceStore(trace_dir)
        self._tracker: Optional[BudgetTracker] = None
        self._trace: Optional[Trace] = None

        if on_patch_failure not in ("block", "warn", "allow"):
            raise ValueError(f"on_patch_failure must be 'block', 'warn', or 'allow', got '{on_patch_failure}'")

        # Cloud sync: explicit args take precedence over env vars
        endpoint = cloud_endpoint or os.environ.get("WICKD_CLOUD_ENDPOINT")
        api_key = cloud_api_key or os.environ.get("WICKD_API_KEY")
        self._cloud_sync: Optional[CloudTraceSync] = None
        if endpoint and api_key:
            self._cloud_sync = CloudTraceSync(endpoint, api_key)

        if self.budget and self.on_budget_kill:
            original_on_kill = self.budget.on_kill
            kill_handler = self.on_budget_kill

            def combined_kill(summary):
                if original_on_kill:
                    original_on_kill(summary)
                kill_handler({
                    "type": "budget_kill",
                    "agent_name": self.name,
                    "spend": summary.get("run_spend", 0),
                    "trigger": "budget_exceeded",
                    "trace_id": self._trace.trace_id if self._trace else "unknown",
                    "summary": summary,
                })

            self.budget.on_kill = combined_kill

        if auto_patch:
            patch_all()
            if on_patch_failure != "allow":
                self._check_patches(on_patch_failure)

        functools.update_wrapper(self, fn)

    def _check_patches(self, failure_mode: str):
        """Verify patches and handle failures based on configured mode."""
        status = verify_patches()
        failed = [
            provider for provider, info in status.items()
            if info["installed"] and not info["verified"]
        ]
        if not failed:
            return
        if failure_mode == "block":
            raise WickdPatchError(failed, status)
        elif failure_mode == "warn":
            providers = ", ".join(failed)
            warnings.warn(
                f"[wickd] Patch verification failed for: {providers}. "
                f"Budget enforcement may not be active for these providers. "
                f"Use on_patch_failure='block' to make this an error.",
                stacklevel=3,
            )

    def run(self, *args, **kwargs) -> Any:
        """Execute the agent with guardrails."""
        self._tracker = BudgetTracker(self.budget) if self.budget else None

        task_str = ""
        if args:
            task_str = str(args[0])[:200]
        elif "task" in kwargs:
            task_str = str(kwargs["task"])[:200]

        self._trace = Trace(agent_name=self.name, task=task_str)
        set_active_tracker(self._tracker)
        set_active_trace(self._trace)

        result = None
        try:
            result = self.fn(*args, **kwargs)
            self._trace.complete(
                budget_summary=self._tracker.summary() if self._tracker else None
            )
        except BudgetExceeded as e:
            self._trace.add_budget_kill(trigger=e.trigger, spend=e.spent)
            self._trace.complete(
                budget_summary=self._tracker.summary() if self._tracker else None
            )
            for handler in self.notify_handlers:
                try:
                    handler({
                        "type": "budget_kill",
                        "agent_name": self.name,
                        "spend": e.spent,
                        "trigger": e.trigger,
                        "trace_id": self._trace.trace_id,
                    })
                except Exception:
                    pass
            raise
        except Exception as e:
            self._trace.status = "error"
            self._trace.events.append(TraceEvent(
                event_type="error",
                error_message=str(e),
                spend_at_event=self._trace.total_cost,
            ))
            self._trace.complete(
                budget_summary=self._tracker.summary() if self._tracker else None
            )
            raise
        finally:
            if self._trace:
                self.trace_store.save(self._trace)
                if self._cloud_sync:
                    self._cloud_sync.sync(self._trace)
            self._print_summary()
            if self.on_run_complete and self._trace:
                try:
                    self.on_run_complete({
                        "type": "run_complete",
                        "agent_name": self.name,
                        "status": self._trace.status,
                        "summary": self._tracker.summary() if self._tracker else {},
                        "trace_id": self._trace.trace_id,
                    })
                except Exception:
                    pass
            set_active_tracker(None)
            set_active_trace(None)

        return result

    def _print_summary(self):
        if not self._trace:
            return

        trace = self._trace
        status_icon = {
            "completed": "\033[32m✓\033[0m",
            "budget_killed": "\033[31m✗ KILLED\033[0m",
            "error": "\033[31m✗ ERROR\033[0m",
        }.get(trace.status, "?")

        parts = [
            f"[wickd] {status_icon} {trace.agent_name}",
            f"${trace.total_cost:.4f} cost",
            f"{sum(1 for e in trace.events if e.event_type == 'llm_call')} calls",
        ]

        if self._tracker and self._tracker.budget.per_run:
            parts.append(
                f"budget: ${self._tracker.run_spend:.4f}/${self._tracker.budget.per_run:.2f}"
            )

        if trace.duration_ms() is not None:
            parts.append(f"{trace.duration_ms():.0f}ms")

        parts.append(f"trace: {trace.trace_id[:8]}")

        print(" | ".join(parts), file=sys.stderr)

    def __call__(self, *args, **kwargs) -> Any:
        return self.run(*args, **kwargs)


def agent(
    fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    budget: Optional[Budget] = None,
    approvals: Optional[list[str]] = None,
    on_budget_kill: Optional[Callable] = None,
    on_run_complete: Optional[Callable] = None,
    notify: Optional[list[Callable]] = None,
    trace_dir: Optional[str] = None,
    auto_patch: bool = True,
    on_patch_failure: str = "warn",
    cloud_endpoint: Optional[str] = None,
    cloud_api_key: Optional[str] = None,
) -> Any:
    """Decorator to wrap a function as a Wickd-guarded agent.

    Args:
        on_patch_failure: How to handle patch verification failures.
            "block" - raise WickdPatchError (recommended for production)
            "warn"  - emit a warning (default)
            "allow" - silent, run unprotected
    """

    def decorator(func: Callable) -> WickdAgent:
        return WickdAgent(
            fn=func,
            name=name,
            budget=budget,
            approvals=approvals,
            on_budget_kill=on_budget_kill,
            on_run_complete=on_run_complete,
            notify=notify,
            trace_dir=trace_dir,
            auto_patch=auto_patch,
            on_patch_failure=on_patch_failure,
            cloud_endpoint=cloud_endpoint,
            cloud_api_key=cloud_api_key,
        )

    if fn is not None:
        return decorator(fn)
    return decorator
