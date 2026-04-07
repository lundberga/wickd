"""Shared ANSI rendering helpers for the Wickd CLI."""

from datetime import datetime


class _C:
    """ANSI colour/style constants."""
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    RED     = "\033[31m"
    GREEN   = "\033[32m"
    YELLOW  = "\033[33m"
    CYAN    = "\033[36m"
    BRED    = "\033[1;31m"
    BGREEN  = "\033[1;32m"
    BYELLOW = "\033[1;33m"
    BCYAN   = "\033[1;36m"


STATUS_ICONS = {
    "completed":     f"{_C.BGREEN}\u2713 completed{_C.RESET}",
    "budget_killed": f"{_C.BRED}\u2717 KILLED{_C.RESET}",
    "error":         f"{_C.BRED}\u2717 ERROR{_C.RESET}",
    "running":       f"{_C.BYELLOW}\u21bb running{_C.RESET}",
}


def format_cost(cost: float) -> str:
    if cost >= 1.0:
        return f"{_C.RED}${cost:.4f}{_C.RESET}"
    elif cost >= 0.10:
        return f"{_C.YELLOW}${cost:.4f}{_C.RESET}"
    return f"{_C.GREEN}${cost:.4f}{_C.RESET}"


def format_cost_raw(cost: float) -> str:
    """Return cost string without ANSI (for width calculations)."""
    return f"${cost:.4f}"


def format_status(status: str) -> str:
    return STATUS_ICONS.get(status, status)


def format_duration(ms: object) -> str:
    """Human-friendly duration from milliseconds."""
    if ms is None:
        return "--"
    ms = float(ms)
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = int(secs // 60)
    remainder = secs - mins * 60
    return f"{mins}m {remainder:.0f}s"


def parse_time(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M")
    except (ValueError, AttributeError):
        return "--"


def progress_bar(current: float, cap: float, width: int = 20) -> str:
    """Render a text progress bar with colour."""
    if cap <= 0:
        return ""
    ratio = min(current / cap, 1.0)
    filled = int(round(ratio * width))
    empty = width - filled
    if ratio >= 1.0:
        colour = _C.RED
    elif ratio >= 0.75:
        colour = _C.YELLOW
    else:
        colour = _C.GREEN
    bar = f"{colour}{'█' * filled}{'░' * empty}{_C.RESET}"
    pct = f"{ratio * 100:.0f}%"
    return f"[{bar}] {pct}"
