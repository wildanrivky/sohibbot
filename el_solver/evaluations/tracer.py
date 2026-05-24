"""
Evaluations Tracer — append-only JSONL trace logging per agent run.

Setiap agent run menghasilkan satu trace entry. Entry di-append ke:
    data/traces/<agent-name>/YYYY-MM-DD.jsonl

Usage:
    tracer = AgentTracer("news-summarizer")
    with tracer.trace("Summarize today's tech news") as span:
        # ... agent logic ...
        span.record_output("Here is the summary...")
        span.add_tool_call("read_file", {"path": "/tmp/news.txt"}, "content...")
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

TRACES_DIR = PROJECT_ROOT / "data" / "traces"


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: dict[str, Any]
    result: Any
    duration_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "result": _truncate(self.result),
            "duration_ms": round(self.duration_ms, 2),
            "error": self.error,
        }


@dataclass
class TraceSpan:
    """
    Satu trace execution span. Diisi secara incremental selama agent run,
    lalu di-flush ke JSONL saat context manager keluar.
    """
    agent_name: str
    input_message: str
    started_at: str = field(default_factory=lambda: _now_iso())
    ended_at: str | None = None
    duration_ms: float | None = None
    output: str | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    plan_summary: str | None = None
    risk_level: str | None = None
    status: str = "running"  # running | success | error
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def record_output(self, output: str) -> None:
        self.output = output

    def add_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        duration_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        self.tool_calls.append(ToolCallRecord(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            duration_ms=duration_ms,
            error=error,
        ))

    def set_plan(self, summary: str, risk_level: str | None = None) -> None:
        self.plan_summary = summary
        self.risk_level = risk_level

    def set_metadata(self, **kwargs: Any) -> None:
        self.metadata.update(kwargs)

    def _finish(self, status: str, error: str | None = None) -> None:
        self.ended_at = _now_iso()
        self.status = status
        self.error = error
        if self.started_at:
            start_ts = datetime.fromisoformat(self.started_at).timestamp()
            self.duration_ms = round((time.time() - start_ts) * 1000, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "input": _truncate(self.input_message, max_len=2000),
            "output": _truncate(self.output, max_len=2000),
            "plan_summary": self.plan_summary,
            "risk_level": self.risk_level,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "tool_call_count": len(self.tool_calls),
            "metadata": self.metadata,
        }


# ── Tracer ────────────────────────────────────────────────────────────────────

class AgentTracer:
    """
    Tracer untuk satu agent. Tiap call ke .trace() membuat satu TraceSpan
    dan menyimpannya ke JSONL saat selesai.
    """

    def __init__(
        self,
        agent_name: str,
        traces_dir: Path | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.traces_dir = (traces_dir or TRACES_DIR) / agent_name
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    def _trace_file(self) -> Path:
        """File path berdasarkan tanggal hari ini."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.traces_dir / f"{date_str}.jsonl"

    def _append(self, span: TraceSpan) -> None:
        """Append satu trace entry ke JSONL file."""
        path = self._trace_file()
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(span.to_dict(), ensure_ascii=False) + "\n")
            logger.debug(
                f"tracer[{self.agent_name}]: appended trace to {path.name} "
                f"(status={span.status}, {span.duration_ms}ms)"
            )
        except OSError as e:
            logger.error(f"tracer[{self.agent_name}]: failed to write trace: {e}")

    @contextmanager
    def trace(self, input_message: str, **metadata: Any) -> Generator[TraceSpan, None, None]:
        """
        Context manager untuk satu trace span.

            with tracer.trace("user message", channel="telegram") as span:
                span.record_output("response")
                span.add_tool_call(...)
            # span otomatis disimpan ke JSONL saat keluar

        Kalau ada exception di dalam block → status=error, exception di-re-raise.
        """
        span = TraceSpan(
            agent_name=self.agent_name,
            input_message=input_message,
            metadata=dict(metadata),
        )
        try:
            yield span
            span._finish("success")
        except Exception as exc:
            span._finish("error", error=str(exc))
            raise
        finally:
            self._append(span)

    def read_traces(self, date: str | None = None) -> list[dict[str, Any]]:
        """
        Baca semua trace dari tanggal tertentu (default: hari ini).
        Return list of dicts (JSONL parsed).
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.traces_dir / f"{date}.jsonl"
        if not path.exists():
            return []
        results = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning(f"tracer: skip invalid JSON line in {path.name}")
        return results

    def list_trace_dates(self) -> list[str]:
        """List semua tanggal yang ada trace-nya."""
        return sorted(
            p.stem for p in self.traces_dir.glob("*.jsonl")
        )

    def rollup(self, dates: list[str] | None = None) -> dict[str, Any]:
        """Read-only aggregation of trace spans for the eval loop (R14 M3).

        Pure read — does not touch the write path. ``dates`` defaults to
        every date with traces for this agent.
        """
        dates = dates if dates is not None else self.list_trace_dates()
        runs = 0
        errors = 0
        total_ms = 0.0
        tool_calls = 0
        for date in dates:
            for tr in self.read_traces(date):
                runs += 1
                if tr.get("status") == "error":
                    errors += 1
                total_ms += float(tr.get("duration_ms") or 0.0)
                tool_calls += int(tr.get("tool_call_count") or 0)
        success = runs - errors
        return {
            "agent": self.agent_name,
            "runs": runs,
            "success": success,
            "errors": errors,
            "success_rate": round(success / runs, 4) if runs else 0.0,
            "avg_duration_ms": round(total_ms / runs, 2) if runs else 0.0,
            "tool_calls": tool_calls,
            "dates": dates,
        }


# ── Global tracer factory ─────────────────────────────────────────────────────

_tracers: dict[str, AgentTracer] = {}


def get_tracer(agent_name: str, traces_dir: Path | None = None) -> AgentTracer:
    """Get-or-create tracer untuk agent tertentu (singleton per agent)."""
    if agent_name not in _tracers:
        _tracers[agent_name] = AgentTracer(agent_name, traces_dir)
    return _tracers[agent_name]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(value: Any, max_len: int = 500) -> Any:
    """Truncate string values agar trace tidak jadi raksasa."""
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + f"...[truncated {len(value) - max_len} chars]"
    return value
