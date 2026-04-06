import asyncio
import functools
import inspect
import logging
import os
import sys
import time
import warnings
from typing import Optional, Callable, Any

logger = logging.getLogger("wickd")

from wickd.budget import Budget, BudgetTracker, BudgetExceeded, WickdPatchError
from wickd.trace import Trace, TraceEvent, TraceStore, CloudTraceSync
from wickd.interceptor import (
    patch_all,
    verify_patches,
    set_active_tracker,
    set_active_trace,
    get_active_tracker,
    get_active_trace,
    _active_tracker,
    _active_trace,
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

        if on_patch_failure not in ("block", "warn", "allow"):
            raise ValueError(f"on_patch_failure must be 'block', 'warn', or 'allow', got '{on_patch_failure}'")

        # Cloud sync: explicit args take precedence over env vars
        endpoint = cloud_endpoint or os.environ.get("WICKD_CLOUD_ENDPOINT")
        api_key = cloud_api_key or os.environ.get("WICKD_API_KEY")
        self._cloud_sync: Optional[CloudTraceSync] = None
        if endpoint and api_key:
            self._cloud_sync = CloudTraceSync(endpoint, api_key)

        if auto_patch:
            patch_all()
            if on_patch_failure != "allow":
                self._check_patches(on_patch_failure)

        functools.update_wrapper(self, fn)

    @property
    def _tracker(self) -> Optional[BudgetTracker]:
        """Active run's tracker for the current async context (backward compat)."""
        return get_active_tracker()

    @property
    def _trace(self) -> Optional[Trace]:
        """Active run's trace for the current async context (backward compat)."""
        return get_active_trace()

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
        """Execute the agent with guardrails. Use arun() for async agent functions."""
        if inspect.iscoroutinefunction(self.fn):
            raise TypeError(
                f"Agent function '{self.name}' is async. Use agent.arun() instead of agent.run()."
            )
        return self._execute(*args, **kwargs)

    async def arun(self, *args, **kwargs) -> Any:
        """Execute an async agent function with guardrails."""
        if not inspect.iscoroutinefunction(self.fn):
            raise TypeError(
                f"Agent function '{self.name}' is sync. Use agent.run() instead of agent.arun()."
            )
        return await self._execute_async(*args, **kwargs)

    async def _execute_async(self, *args, **kwargs) -> Any:
        """Async execution path — context vars propagate correctly across await."""
        tracker = BudgetTracker(self.budget) if self.budget else None

        task_str = ""
        if args:
            task_str = str(args[0])[:200]
        elif "task" in kwargs:
            task_str = str(kwargs["task"])[:200]

        trace = Trace(agent_name=self.name, task=task_str)
        tracker_token = _active_tracker.set(tracker)
        trace_token = _active_trace.set(trace)

        result = None
        try:
            result = await self.fn(*args, **kwargs)
            trace.complete(
                budget_summary=tracker.summary() if tracker else None
            )
        except BudgetExceeded as e:
            trace.add_budget_kill(trigger=e.trigger, spend=e.spent)
            trace.complete(
                budget_summary=tracker.summary() if tracker else None
            )
            if self.on_budget_kill:
                try:
                    self.on_budget_kill({
                        "type": "budget_kill",
                        "agent_name": self.name,
                        "spend": e.spent,
                        "trigger": e.trigger,
                        "trace_id": trace.trace_id,
                    })
                except Exception as handler_err:
                    logger.warning("Wickd handler failed: %s", handler_err)
            for handler in self.notify_handlers:
                try:
                    handler({
                        "type": "budget_kill",
                        "agent_name": self.name,
                        "spend": e.spent,
                        "trigger": e.trigger,
                        "trace_id": trace.trace_id,
                    })
                except Exception as handler_err:
                    logger.warning("Wickd handler failed: %s", handler_err)
            raise
        except Exception as e:
            trace.status = "error"
            trace.events.append(TraceEvent(
                event_type="error",
                error_message=str(e),
                spend_at_event=trace.total_cost,
            ))
            trace.complete(
                budget_summary=tracker.summary() if tracker else None
            )
            raise
        finally:
            self.trace_store.save(trace)
            if self._cloud_sync:
                self._cloud_sync.sync(trace)
            self._print_summary(tracker, trace)
            if self.on_run_complete:
                try:
                    self.on_run_complete({
                        "type": "run_complete",
                        "agent_name": self.name,
                        "status": trace.status,
                        "summary": tracker.summary() if tracker else {},
                        "trace_id": trace.trace_id,
                    })
                except Exception as handler_err:
                    logger.warning("Wickd handler failed: %s", handler_err)
            _active_tracker.reset(tracker_token)
            _active_trace.reset(trace_token)

        return result

    def _execute(self, *args, **kwargs) -> Any:
        """Sync execution path."""
        tracker = BudgetTracker(self.budget) if self.budget else None

        task_str = ""
        if args:
            task_str = str(args[0])[:200]
        elif "task" in kwargs:
            task_str = str(kwargs["task"])[:200]

        trace = Trace(agent_name=self.name, task=task_str)

        # Set per-run context, capturing tokens for correct restoration even
        # when runs are interleaved in async code.
        tracker_token = _active_tracker.set(tracker)
        trace_token = _active_trace.set(trace)

        result = None
        try:
            result = self.fn(*args, **kwargs)
            trace.complete(
                budget_summary=tracker.summary() if tracker else None
            )
        except BudgetExceeded as e:
            trace.add_budget_kill(trigger=e.trigger, spend=e.spent)
            trace.complete(
                budget_summary=tracker.summary() if tracker else None
            )
            if self.on_budget_kill:
                try:
                    self.on_budget_kill({
                        "type": "budget_kill",
                        "agent_name": self.name,
                        "spend": e.spent,
                        "trigger": e.trigger,
                        "trace_id": trace.trace_id,
                    })
                except Exception as handler_err:
                    logger.warning("Wickd handler failed: %s", handler_err)
            for handler in self.notify_handlers:
                try:
                    handler({
                        "type": "budget_kill",
                        "agent_name": self.name,
                        "spend": e.spent,
                        "trigger": e.trigger,
                        "trace_id": trace.trace_id,
                    })
                except Exception as handler_err:
                    logger.warning("Wickd handler failed: %s", handler_err)
            raise
        except Exception as e:
            trace.status = "error"
            trace.events.append(TraceEvent(
                event_type="error",
                error_message=str(e),
                spend_at_event=trace.total_cost,
            ))
            trace.complete(
                budget_summary=tracker.summary() if tracker else None
            )
            raise
        finally:
            self.trace_store.save(trace)
            if self._cloud_sync:
                self._cloud_sync.sync(trace)
            self._print_summary(tracker, trace)
            if self.on_run_complete:
                try:
                    self.on_run_complete({
                        "type": "run_complete",
                        "agent_name": self.name,
                        "status": trace.status,
                        "summary": tracker.summary() if tracker else {},
                        "trace_id": trace.trace_id,
                    })
                except Exception as handler_err:
                    logger.warning("Wickd handler failed: %s", handler_err)
            # Restore the previous context values so nested/sequential runs
            # are isolated without clobbering a parent run's context.
            _active_tracker.reset(tracker_token)
            _active_trace.reset(trace_token)

        return result

    def _print_summary(self, tracker: Optional[BudgetTracker], trace: Trace) -> None:
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

        if tracker and tracker.budget.per_run:
            parts.append(
                f"budget: ${tracker.run_spend:.4f}/${tracker.budget.per_run:.2f}"
            )

        if trace.duration_ms() is not None:
            parts.append(f"{trace.duration_ms():.0f}ms")

        parts.append(f"trace: {trace.trace_id[:8]}")

        print(" | ".join(parts), file=sys.stderr)

    def __call__(self, *args, **kwargs) -> Any:
        if inspect.iscoroutinefunction(self.fn):
            return self.arun(*args, **kwargs)
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
