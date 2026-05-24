"""Daily / weekly cadence digests (R17 M3).

- ``daily_standup``  : per-agent ("department") activity digest from the
  tracer rollup — the "Department Heads run their own daily standup"
  milestone, expressed as one consolidated standup.
- ``weekly_review``  : portfolio review + self-eval + initiatives stitched
  into the Monday brief.

Deterministic, LLM-free, side-effect-free (pure string builders).
"""
from __future__ import annotations

from datetime import UTC, datetime

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


def daily_standup(trace_days: int = 1) -> str:
    """One-line-per-agent standup from the tracer rollup."""
    now = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [f"# Daily Standup — {now}", ""]
    try:
        from el_solver.core.eval import list_traced_agents, rollup_agent

        agents = list_traced_agents()
        if not agents:
            lines.append("_(belum ada aktivitas agent)_")
        for agent in agents:
            r = rollup_agent(agent, trace_days)
            if r["runs"]:
                lines.append(
                    f"- {agent}: {r['runs']} runs, "
                    f"{r['success_rate'] * 100:.0f}% ok, "
                    f"{r['avg_duration_ms']:.0f}ms avg"
                )
            else:
                lines.append(f"- {agent}: idle")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"cadence: standup rollup unavailable ({exc})")
        lines.append("_(rollup tidak tersedia)_")
    return "\n".join(lines)


def weekly_review() -> str:
    """Monday brief: portfolio review + self-eval + initiatives."""
    parts: list[str] = []
    try:
        from el_solver.core.portfolio_planner import weekly_portfolio_review

        parts.append(weekly_portfolio_review().to_markdown())
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"cadence: portfolio review unavailable ({exc})")

    try:
        from el_solver.core.self_eval import self_eval_report

        parts.append(self_eval_report())
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"cadence: self_eval unavailable ({exc})")

    try:
        from el_solver.core.initiative import (
            render_initiatives,
            scan_initiatives,
        )

        parts.append(
            "# Initiatives\n\n" + render_initiatives(scan_initiatives())
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"cadence: initiatives unavailable ({exc})")

    return "\n\n---\n\n".join(parts) if parts else "_(weekly review kosong)_"
