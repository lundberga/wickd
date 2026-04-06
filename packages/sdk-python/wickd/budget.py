import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger("wickd")


class BudgetExceeded(Exception):
    """Raised when an agent exceeds its budget cap."""

    def __init__(self, budget: "Budget", spent: float, trigger: str):
        self.budget = budget
        self.spent = spent
        self.trigger = trigger
        cap_attr = trigger.replace("_cap", "")
        cap_value = getattr(budget, cap_attr, None)
        if cap_value is not None:
            msg = f"Budget exceeded: ${spent:.4f} spent, {trigger} cap of ${cap_value:.2f} hit."
        else:
            msg = f"Budget exceeded: ${spent:.4f} spent ({trigger})."
        super().__init__(msg)


class WickdPatchError(Exception):
    """Raised when SDK patches fail and on_patch_failure='block'.

    Contains diagnostic info about which providers failed to patch.
    """

    def __init__(self, failed_providers: list[str], status: dict):
        self.failed_providers = failed_providers
        self.status = status
        providers = ", ".join(failed_providers)
        msg = (
            f"Wickd patch verification failed for: {providers}. "
            f"Budget enforcement is NOT active for these providers. "
            f"Set on_patch_failure='warn' to downgrade to a warning, "
            f"or on_patch_failure='allow' to run unprotected."
        )
        super().__init__(msg)


@dataclass
class Budget:
    """Budget caps for an agent. All values in USD."""

    per_run: Optional[float] = None
    daily: Optional[float] = None
    monthly: Optional[float] = None
    on_kill: Optional[Callable] = None

    def __post_init__(self):
        for cap in ("per_run", "daily", "monthly"):
            val = getattr(self, cap)
            if val is not None and val <= 0:
                raise ValueError(f"{cap} budget must be positive")


class BudgetTracker:
    """Thread-safe real-time budget tracking and enforcement."""

    def __init__(self, budget: Budget):
        self.budget = budget
        self._lock = threading.RLock()
        self._run_spend = 0.0
        self._daily_spend = 0.0
        self._monthly_spend = 0.0
        self._call_count = 0
        self._run_start = time.time()
        self._killed = False

    @property
    def run_spend(self) -> float:
        return self._run_spend

    @property
    def daily_spend(self) -> float:
        return self._daily_spend

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def is_killed(self) -> bool:
        return self._killed

    def remaining(self) -> Optional[float]:
        """Smallest remaining budget across all caps, or None if uncapped."""
        remainders = []
        if self.budget.per_run is not None:
            remainders.append(self.budget.per_run - self._run_spend)
        if self.budget.daily is not None:
            remainders.append(self.budget.daily - self._daily_spend)
        if self.budget.monthly is not None:
            remainders.append(self.budget.monthly - self._monthly_spend)
        return min(remainders) if remainders else None

    def check_budget(self):
        """Raises BudgetExceeded if any cap is hit."""
        with self._lock:
            self._check_budget_locked()

    def _check_budget_locked(self):
        """Inner budget check -- caller must hold self._lock."""
        if self._killed:
            raise BudgetExceeded(self.budget, self._run_spend, "already_killed")

        if self.budget.per_run is not None and self._run_spend >= self.budget.per_run:
            self._kill("per_run")
        if self.budget.daily is not None and self._daily_spend >= self.budget.daily:
            self._kill("daily")
        if self.budget.monthly is not None and self._monthly_spend >= self.budget.monthly:
            self._kill("monthly")

    def record_cost(self, cost: float, model: str = "", input_tokens: int = 0, output_tokens: int = 0):
        """Record a cost event from an LLM call and check budgets."""
        with self._lock:
            self._run_spend += cost
            self._daily_spend += cost
            self._monthly_spend += cost
            self._call_count += 1
            self._check_budget_locked()

    def pre_call_check(self):
        """Check budget before making an LLM call."""
        self.check_budget()

    def reset_run(self):
        """Reset per-run counters for a new run."""
        with self._lock:
            self._run_spend = 0.0
            self._call_count = 0
            self._run_start = time.time()
            self._killed = False

    def summary(self) -> dict:
        return {
            "run_spend": round(self._run_spend, 6),
            "daily_spend": round(self._daily_spend, 6),
            "monthly_spend": round(self._monthly_spend, 6),
            "call_count": self._call_count,
            "remaining": self.remaining(),
            "killed": self._killed,
            "caps": {
                "per_run": self.budget.per_run,
                "daily": self.budget.daily,
                "monthly": self.budget.monthly,
            },
        }

    def _kill(self, trigger: str):
        self._killed = True
        if self.budget.on_kill:
            try:
                self.budget.on_kill(self.summary())
            except Exception as e:
                logger.warning("Wickd handler failed: %s", e)
        raise BudgetExceeded(self.budget, self._run_spend, trigger)
