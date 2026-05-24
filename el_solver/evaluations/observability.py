"""
Cross-agent Observability — korelasi workflow run dengan per-agent traces.

Setiap workflow run punya ID (run_id). WorkflowEngine menyimpan step results
ke SQLite. Agent yang dipanggil di dalam workflow bisa menyimpan trace JSONL
dengan metadata.workflow_run_id = run_id agar bisa dikorelasikan.

Usage:
    engine = ObservabilityEngine()
    spans = engine.get_workflow_spans("abc12345")
    print(engine.format_report(spans))
    stats = engine.get_agent_stats("news-agent", days=7)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from el_solver.config import PROJECT_ROOT
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

TRACES_DIR = PROJECT_ROOT / "data" / "traces"


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class WorkflowSpan:
    """
    Correlated view: satu workflow step + matching JSONL trace entries (opsional).
    """
    run_id: str
    step_id: str
    agent_name: str
    status: str
    duration_ms: float
    input_snapshot: str
    output: str
    error: str | None
    trace_entries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    @property
    def has_traces(self) -> bool:
        return len(self.trace_entries) > 0


@dataclass
class AgentStats:
    """Statistik satu agent dalam rentang waktu tertentu."""
    agent_name: str
    total_runs: int
    success_runs: int
    error_runs: int
    avg_duration_ms: float
    total_duration_ms: float
    unique_workflows: int

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.success_runs / self.total_runs

    @property
    def error_rate(self) -> float:
        return 1.0 - self.success_rate


# ── Core functions ────────────────────────────────────────────────────────────

def _find_correlated_traces(
    agent_name: str,
    run_id: str,
    traces_dir: Path,
) -> list[dict[str, Any]]:
    """
    Cari JSONL trace entries untuk agent yang metadata.workflow_run_id == run_id.
    Scan semua .jsonl file di traces_dir/<agent_name>/.
    """
    agent_dir = traces_dir / agent_name
    if not agent_dir.exists():
        return []

    matches: list[dict[str, Any]] = []
    for jsonl_file in sorted(agent_dir.glob("*.jsonl")):
        try:
            for raw_line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if entry.get("metadata", {}).get("workflow_run_id") == run_id:
                    matches.append(entry)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug(f"observability: skip {jsonl_file.name}: {e}")
    return matches


# ── Observability Engine ──────────────────────────────────────────────────────

class ObservabilityEngine:
    """
    Query interface untuk cross-agent observability.
    Menggabungkan data dari SQLite (workflow_step_results) dengan JSONL traces.
    """

    def __init__(self, traces_dir: Path | None = None) -> None:
        self.traces_dir = traces_dir or TRACES_DIR

    def get_workflow_spans(self, run_id: str) -> list[WorkflowSpan]:
        """
        Load semua step results untuk satu workflow run.
        Korelasikan dengan JSONL traces kalau ada.

        Returns:
            List[WorkflowSpan] terurut berdasarkan step execution order.
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT step_id, agent_name, status, input_snapshot,
                          output, error, duration_ms
                   FROM workflow_step_results
                   WHERE run_id=? ORDER BY id""",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()

        spans: list[WorkflowSpan] = []
        for r in rows:
            traces = _find_correlated_traces(
                r["agent_name"], run_id, self.traces_dir
            )
            spans.append(WorkflowSpan(
                run_id=run_id,
                step_id=r["step_id"],
                agent_name=r["agent_name"],
                status=r["status"],
                duration_ms=r["duration_ms"] or 0.0,
                input_snapshot=r["input_snapshot"] or "",
                output=r["output"] or "",
                error=r["error"],
                trace_entries=traces,
            ))

        logger.debug(
            f"observability: run {run_id} → {len(spans)} spans, "
            f"{sum(1 for s in spans if s.has_traces)} with traces"
        )
        return spans

    def get_workflow_run_summary(self, run_id: str) -> dict[str, Any] | None:
        """
        Baca summary workflow run dari DB.
        Return None kalau run_id tidak ditemukan.
        """
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT run_id, workflow_name, status, started_at, finished_at, error "
                "FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()

    def get_agent_stats(
        self,
        agent_name: str,
        limit: int = 100,
    ) -> AgentStats:
        """
        Hitung statistik eksekusi satu agent dari workflow_step_results.
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT status, duration_ms, run_id
                   FROM workflow_step_results
                   WHERE agent_name=?
                   ORDER BY id DESC LIMIT ?""",
                (agent_name, limit),
            ).fetchall()
        finally:
            conn.close()

        total = len(rows)
        if total == 0:
            return AgentStats(
                agent_name=agent_name,
                total_runs=0,
                success_runs=0,
                error_runs=0,
                avg_duration_ms=0.0,
                total_duration_ms=0.0,
                unique_workflows=0,
            )

        success = sum(1 for r in rows if r["status"] == "success")
        durations = [r["duration_ms"] or 0.0 for r in rows]
        total_ms = sum(durations)
        unique_wf = len({r["run_id"] for r in rows})

        return AgentStats(
            agent_name=agent_name,
            total_runs=total,
            success_runs=success,
            error_runs=total - success,
            avg_duration_ms=total_ms / total,
            total_duration_ms=total_ms,
            unique_workflows=unique_wf,
        )

    def list_workflow_runs(
        self,
        workflow_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent workflow runs, optionally filter by workflow name."""
        conn = get_connection()
        try:
            if workflow_name:
                rows = conn.execute(
                    "SELECT run_id, workflow_name, status, started_at, finished_at "
                    "FROM workflow_runs WHERE workflow_name=? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (workflow_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT run_id, workflow_name, status, started_at, finished_at "
                    "FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def format_report(self, spans: list[WorkflowSpan]) -> str:
        """
        Format workflow spans sebagai human-readable report.
        Cocok untuk log / Telegram notification.
        """
        if not spans:
            return "No spans found."

        lines: list[str] = [f"Workflow Run: {spans[0].run_id}", ""]
        for span in spans:
            icon = "✓" if span.succeeded else "✗"
            lines.append(
                f"  {icon} [{span.step_id}] {span.agent_name} "
                f"— {span.status} ({span.duration_ms:.0f}ms)"
            )
            if span.error:
                lines.append(f"      Error: {span.error[:120]}")
            if span.has_traces:
                lines.append(f"      Traces: {len(span.trace_entries)} correlated")

        total_ms = sum(s.duration_ms for s in spans)
        succeeded = sum(1 for s in spans if s.succeeded)
        lines.extend([
            "",
            f"Total: {len(spans)} steps | {succeeded} ok | "
            f"{len(spans) - succeeded} failed | {total_ms:.0f}ms",
        ])
        return "\n".join(lines)

    def format_telegram_summary(self, spans: list[WorkflowSpan]) -> str:
        """Compact format untuk Telegram notification."""
        if not spans:
            return "No data."
        total = len(spans)
        ok = sum(1 for s in spans if s.succeeded)
        total_ms = sum(s.duration_ms for s in spans)
        status = "✅" if ok == total else "⚠️" if ok > 0 else "❌"
        return (
            f"{status} Workflow {spans[0].run_id}: "
            f"{ok}/{total} steps ok, {total_ms:.0f}ms"
        )
