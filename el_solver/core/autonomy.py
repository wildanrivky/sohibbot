"""Autonomy / touch-time metric (R17 M5).

Blueprint R17 M5 success = Wildan's operational touch-time down >= 50%.
We can't measure wall-clock overnight, so this exposes the deterministic
proxy that *would* track it longitudinally:

  - decision autonomous-rate (ACT_* / all decisions, from the audit log)
  - cycle handled-rate (initiatives acted vs queued, from a dry cycle)

`touch_time_reduction_estimate` blends them into a single 0..1 figure
plus a plain-language interpretation for the weekly brief.
"""
from __future__ import annotations

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

TARGET_REDUCTION = 0.5  # blueprint R17 M5


def autonomy_metric(days: int = 30) -> dict:
    """Decision autonomous-rate + initiative cycle handled-rate."""
    autonomous_rate = 0.0
    total_decisions = 0
    try:
        from el_solver.core.decision_engine import retrospective

        retro = retrospective(days=days)
        autonomous_rate = retro["autonomous_rate"]
        total_decisions = retro["total"]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"autonomy: retrospective unavailable ({exc})")

    acted = queued = 0
    try:
        from el_solver.core.initiative import autonomous_cycle

        rep = autonomous_cycle()
        acted, queued = len(rep.acted), len(rep.queued)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"autonomy: cycle unavailable ({exc})")

    cycle_total = acted + queued
    cycle_handled_rate = round(acted / cycle_total, 4) if cycle_total else 0.0

    # blended estimate: weight observed decisions if any, else fall back to
    # the cycle signal.
    if total_decisions:
        estimate = round(0.6 * autonomous_rate + 0.4 * cycle_handled_rate, 4)
    else:
        estimate = cycle_handled_rate

    return {
        "days": days,
        "total_decisions": total_decisions,
        "autonomous_rate": autonomous_rate,
        "cycle_acted": acted,
        "cycle_queued": queued,
        "cycle_handled_rate": cycle_handled_rate,
        "touch_time_reduction_estimate": estimate,
        "meets_target": estimate >= TARGET_REDUCTION,
    }


def autonomy_report(days: int = 30) -> str:
    m = autonomy_metric(days)
    verdict = (
        "✅ target ≥50% tercapai (estimasi)"
        if m["meets_target"]
        else "⏳ belum mencapai target 50% — masih banyak yang perlu Wildan"
    )
    return "\n".join(
        [
            f"# Autonomy Metric ({m['days']}d)",
            "",
            f"- decisions: {m['total_decisions']} "
            f"(autonomous {m['autonomous_rate']:.0%})",
            f"- initiative cycle: {m['cycle_acted']} acted / "
            f"{m['cycle_queued']} queued "
            f"({m['cycle_handled_rate']:.0%} handled)",
            f"- touch-time reduction estimate: "
            f"**{m['touch_time_reduction_estimate']:.0%}**",
            "",
            verdict,
        ]
    )
