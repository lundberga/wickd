import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("wickd")

MAX_PREVIEW = 200


@dataclass
class TraceEvent:
    """A single event in an agent trace.

    Fields are grouped by event type. Only populated fields are serialized.
    """

    event_type: str  # llm_call | tool_call | approval_gate | budget_kill | error
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # LLM call
    model: Optional[str] = None
    provider: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost: Optional[float] = None
    latency_ms: Optional[float] = None
    prompt_preview: Optional[str] = None
    response_preview: Optional[str] = None

    # Tool call (MCP or function call)
    tool_name: Optional[str] = None
    tool_input_preview: Optional[str] = None
    tool_output_preview: Optional[str] = None
    tool_status: Optional[str] = None  # "success", "error", "denied"
    tool_server: Optional[str] = None  # MCP server name/origin

    # Approval
    approval_name: Optional[str] = None
    approval_status: Optional[str] = None

    # Budget
    budget_trigger: Optional[str] = None
    spend_at_event: Optional[float] = None

    # Error
    error_message: Optional[str] = None

    metadata: Optional[dict] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Trace:
    """A complete trace of one agent run."""

    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_name: str = ""
    task: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    events: list[TraceEvent] = field(default_factory=list)
    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    status: str = "running"
    budget_summary: Optional[dict] = None

    def add_event(self, event: TraceEvent):
        self.events.append(event)
        if event.cost:
            self.total_cost += event.cost
        if event.input_tokens:
            self.total_input_tokens += event.input_tokens
        if event.output_tokens:
            self.total_output_tokens += event.output_tokens

    def add_llm_call(
        self,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        latency_ms: float,
        prompt_preview: str = "",
        response_preview: str = "",
    ) -> TraceEvent:
        event = TraceEvent(
            event_type="llm_call",
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            latency_ms=latency_ms,
            prompt_preview=prompt_preview[:MAX_PREVIEW] if prompt_preview else None,
            response_preview=response_preview[:MAX_PREVIEW] if response_preview else None,
            spend_at_event=self.total_cost + cost,
        )
        self.add_event(event)
        return event

    def add_approval(self, name: str, status: str) -> TraceEvent:
        event = TraceEvent(
            event_type="approval_gate",
            approval_name=name,
            approval_status=status,
            spend_at_event=self.total_cost,
        )
        self.add_event(event)
        return event

    def add_tool_call(
        self,
        tool_name: str,
        latency_ms: float,
        tool_input: str = "",
        tool_output: str = "",
        tool_status: str = "success",
        tool_server: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> TraceEvent:
        """Record an MCP or function tool call in the trace."""
        meta = {"tool": tool_name}
        if tool_server:
            meta["server"] = tool_server
        if metadata:
            meta.update(metadata)
        event = TraceEvent(
            event_type="tool_call",
            tool_name=tool_name,
            tool_input_preview=tool_input[:MAX_PREVIEW] if tool_input else None,
            tool_output_preview=tool_output[:MAX_PREVIEW] if tool_output else None,
            tool_status=tool_status,
            tool_server=tool_server,
            latency_ms=latency_ms,
            spend_at_event=self.total_cost,
            metadata=meta,
        )
        self.add_event(event)
        return event

    def add_budget_kill(self, trigger: str, spend: float) -> TraceEvent:
        event = TraceEvent(
            event_type="budget_kill",
            budget_trigger=trigger,
            spend_at_event=spend,
        )
        self.add_event(event)
        self.status = "budget_killed"
        return event

    def complete(self, budget_summary: Optional[dict] = None):
        self.ended_at = time.time()
        if self.status == "running":
            self.status = "completed"
        self.budget_summary = budget_summary

    def duration_ms(self) -> Optional[float]:
        if self.ended_at:
            return round((self.ended_at - self.started_at) * 1000, 1)
        return None

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "agent_name": self.agent_name,
            "task": self.task,
            "started_at": datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat(),
            "ended_at": datetime.fromtimestamp(self.ended_at, tz=timezone.utc).isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms(),
            "status": self.status,
            "total_cost": round(self.total_cost, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "llm_calls": sum(1 for e in self.events if e.event_type == "llm_call"),
            "tool_calls": sum(1 for e in self.events if e.event_type == "tool_call"),
            "events": [e.to_dict() for e in self.events],
            "budget_summary": self.budget_summary,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class TraceStore:
    """Saves traces as JSON to ~/.wickd/traces/."""

    def __init__(self, trace_dir: Optional[str] = None):
        self.trace_dir = Path(trace_dir) if trace_dir else Path.home() / ".wickd" / "traces"
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def save(self, trace: Trace) -> Path:
        filepath = self.trace_dir / f"{trace.agent_name}_{trace.trace_id[:8]}.json"
        filepath.write_text(trace.to_json())
        return filepath

    def list_traces(self, limit: int = 20) -> list[dict]:
        files = sorted(self.trace_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        traces = []
        for f in files[:limit]:
            try:
                traces.append(json.loads(f.read_text()))
            except (json.JSONDecodeError, OSError):
                continue
        return traces

    def get_trace(self, trace_id: str) -> Optional[dict]:
        for f in self.trace_dir.glob("*.json"):
            if trace_id in f.name:
                try:
                    return json.loads(f.read_text())
                except (json.JSONDecodeError, OSError):
                    return None
        return None


