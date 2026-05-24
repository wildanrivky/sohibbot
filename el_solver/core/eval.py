"""Eval rollup + weekly report (R14 M3).

Turns the append-only tracer JSONL into agent-performance metrics and a
human-readable weekly report that also surfaces the latest KPI snapshots
per portfolio metric. This is the "is this working?" layer the blueprint
(Section 9) is missing today.

No new dependencies: reads tracer files + kpi_snapshots only.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from el_solver.core.kpi_ingest import KpiPoint

from el_solver.evaluations.tracer import TRACES_DIR, AgentTracer
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


def _recent_dates(days: int) -> list[str]:
    today = datetime.now(UTC).date()
    return [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def list_traced_agents(traces_dir: Path | None = None) -> list[str]:
    base = traces_dir or TRACES_DIR
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if d.is_dir())


def rollup_agent(
    agent: str,
    days: int = 7,
    traces_dir: Path | None = None,
) -> dict[str, Any]:
    """Performance rollup for one agent over the last ``days`` days."""
    tracer = AgentTracer(agent, traces_dir)
    return tracer.rollup(_recent_dates(days))


def rollup_all(
    days: int = 7,
    traces_dir: Path | None = None,
    *,
    emit: bool = True,
) -> list[dict[str, Any]]:
    """Rollups for every traced agent. Emits an ``eval.rollup`` event."""
    rollups = [
        rollup_agent(agent, days, traces_dir)
        for agent in list_traced_agents(traces_dir)
    ]
    if emit:
        try:
            from el_solver.core.events import emit_event

            emit_event(
                "eval.rollup",
                {
                    "days": days,
                    "agents": len(rollups),
                    "total_runs": sum(r["runs"] for r in rollups),
                },
            )
        except Exception as exc:  # noqa: BLE001 — telemetry must not break eval
            logger.debug(f"eval: emit rollup event failed ({exc})")
    return rollups


def _kpi_section() -> list[str]:
    """Latest snapshot per metric declared in portfolio.md (best-effort)."""
    try:
        from el_solver.config import PROJECT_ROOT
        from el_solver.core.kpi_ingest import recent
    except Exception:  # noqa: BLE001
        return ["_(KPI module tidak tersedia)_"]

    portfolio = PROJECT_ROOT / "memory" / "business" / "portfolio.md"
    metrics: list[str] = []
    if portfolio.exists():
        text = portfolio.read_text(encoding="utf-8")
        for token in text.replace("`", " ").split():
            t = token.strip(",")
            normalized = t.replace(".", "").replace("_", "").replace("-", "")
            if "." in t and normalized.isalnum() and t not in metrics:
                metrics.append(t)

    lines: list[str] = []
    by_metric: dict[str, KpiPoint] = {}
    for point in recent(limit=200):
        by_metric.setdefault(point.metric, point)  # recent() is newest-first
    for m in metrics:
        pt = by_metric.get(m)
        if pt is None:
            lines.append(f"- {m}: _(belum ada data)_")
        else:
            lines.append(f"- {m}: **{pt.value}** ({pt.ts[:10]})")
    return lines or ["_(portfolio.md tidak punya metrik)_"]


def weekly_report(days: int = 7, traces_dir: Path | None = None) -> str:
    """Markdown report: agent performance + latest KPIs. Returns the text."""
    rollups = rollup_all(days, traces_dir)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# Weekly Report — {now}",
        "",
        f"## Agent Performance (last {days}d)",
    ]
    if not rollups:
        lines.append("_(belum ada trace agent)_")
    else:
        lines.append("| Agent | Runs | Success | Err | Succ% | Avg ms |")
        lines.append("|---|---|---|---|---|---|")
        for r in rollups:
            lines.append(
                f"| {r['agent']} | {r['runs']} | {r['success']} | "
                f"{r['errors']} | {r['success_rate'] * 100:.0f}% | "
                f"{r['avg_duration_ms']:.0f} |"
            )
    lines += ["", "## KPI Snapshot (latest per metric)"]
    lines += _kpi_section()
    lines += ["", "## Causal Priors (decision input)"]
    try:
        from el_solver.core.causal import report_section

        lines += report_section()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"eval: causal section unavailable ({exc})")
        lines.append("_(causal model tidak tersedia)_")
    return "\n".join(lines)
