"""Skill-gap analysis (R16 M4, blueprint 9.7).

Aggregates three deterministic signals into a ranked list of capability
gaps El Solver should close:

  1. golden low-performers (`self_eval`)
  2. agents with poor tracer success-rate
  3. recurring agent failures (repeated error traces)

No LLM. Output feeds a Wildan-facing skill-acquisition recommendation;
nothing is auto-acquired.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

POOR_SUCCESS_RATE = 0.7
MIN_RUNS_FOR_SIGNAL = 3


@dataclass
class SkillGap:
    area: str          # agent or capability id
    evidence: str
    severity: int      # 1..5
    suggested_action: str


def skill_gaps(
    runners: dict | None = None,
    trace_days: int = 14,
    golden_root: Path | None = None,
) -> list[SkillGap]:
    """Ranked skill gaps (highest severity first)."""
    gaps: list[SkillGap] = []

    # (1) golden low-performers
    try:
        from el_solver.core.self_eval import score_all

        for s in score_all(runners, golden_root):
            if s.is_low_performer:
                gaps.append(
                    SkillGap(
                        area=s.agent,
                        evidence=f"golden pass-rate {s.pass_rate:.0%} "
                        f"({s.passed}/{s.total})",
                        severity=4,
                        suggested_action="prompt mutation A/B (R16 M3) atau "
                        "tambah/curate golden cases",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"skill_gap: self_eval unavailable ({exc})")

    # (2) poor tracer success-rate / (3) repeated failures
    try:
        from el_solver.core.eval import list_traced_agents, rollup_agent

        for agent in list_traced_agents():
            r = rollup_agent(agent, trace_days)
            if r["runs"] >= MIN_RUNS_FOR_SIGNAL and (
                r["success_rate"] < POOR_SUCCESS_RATE
            ):
                gaps.append(
                    SkillGap(
                        area=agent,
                        evidence=f"tracer success {r['success_rate']:.0%} "
                        f"over {r['runs']} runs",
                        severity=3 if r["success_rate"] >= 0.4 else 5,
                        suggested_action="root-cause failing traces; "
                        "consider tool/skill addition",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"skill_gap: tracer rollup unavailable ({exc})")

    gaps.sort(key=lambda g: g.severity, reverse=True)
    return gaps


def skill_gap_report(
    runners: dict | None = None,
    trace_days: int = 14,
) -> str:
    gaps = skill_gaps(runners, trace_days)
    if not gaps:
        return "# Skill-Gap Report\n\n_(tidak ada gap terdeteksi)_"
    lines = ["# Skill-Gap Report", ""]
    for g in gaps:
        lines.append(
            f"- **{g.area}** [sev {g.severity}] — {g.evidence}\n"
            f"  → {g.suggested_action}"
        )
    return "\n".join(lines)
