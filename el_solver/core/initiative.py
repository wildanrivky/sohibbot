"""Initiative engine (R17 M2).

The autonomous-GM behaviour: scan portfolio/KPI/causal/skill signals and
surface "I notice X → I recommend Y" proposals, each risk-tiered through
the R15 decision engine so reversible low-stakes nudges can act while
anything heavier is proposed/escalated. Deterministic, LLM-free.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from el_solver.core.decision_engine import DecisionInput, Policy, decide
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Initiative:
    notice: str
    recommendation: str
    policy: Policy
    rationale: str
    source: str

    def render(self) -> str:
        return (
            f"Saya perhatikan: {self.notice}\n"
            f"Saya rekomendasikan: {self.recommendation}\n"
            f"[{self.policy.value}] {self.rationale}"
        )


def _tier(action: str, severity: int, confidence: float) -> tuple[Policy, str]:
    out = decide(
        DecisionInput(
            action=action,
            severity=severity,
            probability=0.5,
            irreversibility=1,
            confidence=confidence,
            reversible=True,
        )
    )
    return out.policy, out.rationale


def scan_initiatives(
    runners: dict | None = None,
    memory_root: Path | None = None,
) -> list[Initiative]:
    """Generate risk-tiered initiative proposals from current state."""
    initiatives: list[Initiative] = []

    # (1) portfolio metrics with no KPI data yet
    try:
        from el_solver.core.portfolio_planner import weekly_portfolio_review

        for u in weekly_portfolio_review(memory_root).units:
            if u.status == "no-data":
                pol, rat = _tier(
                    f"prompt Wildan to start logging {u.metric}",
                    severity=2,
                    confidence=0.9,
                )
                initiatives.append(
                    Initiative(
                        notice=f"`{u.metric}` belum punya data KPI sama sekali",
                        recommendation=(
                            f"mulai `el kpi log {u.metric} <nilai>` agar "
                            "review mingguan punya sinyal"
                        ),
                        policy=pol,
                        rationale=rat,
                        source="portfolio",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"initiative: portfolio scan failed ({exc})")

    # (2) unvalidated causal priors
    try:
        from el_solver.core.causal import parse_causal_model

        for e in parse_causal_model(memory_root):
            if not e.is_confirmed:
                pol, rat = _tier(
                    f"collect observations for causal prior {e.id}",
                    severity=2,
                    confidence=0.8,
                )
                initiatives.append(
                    Initiative(
                        notice=(
                            f"prior `{e.id}` masih {e.confidence_tier()} "
                            f"(obs={e.observations})"
                        ),
                        recommendation=(
                            f"kumpulkan ≥3 observasi untuk `{e.metric}` "
                            "sebelum dijadikan dasar keputusan"
                        ),
                        policy=pol,
                        rationale=rat,
                        source="causal",
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"initiative: causal scan failed ({exc})")

    # (3) skill gaps (low performers etc.)
    try:
        from el_solver.core.skill_gap import skill_gaps

        for g in skill_gaps(runners):
            pol, rat = _tier(
                f"address skill gap in {g.area}",
                severity=g.severity,
                confidence=0.7,
            )
            initiatives.append(
                Initiative(
                    notice=f"skill gap: {g.area} — {g.evidence}",
                    recommendation=g.suggested_action,
                    policy=pol,
                    rationale=rat,
                    source="skill_gap",
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"initiative: skill_gap scan failed ({exc})")

    return initiatives


_AUTONOMOUS = {Policy.ACT_LOG, Policy.ACT_NOTIFY}

# executor(initiative) -> str (what was done). Injected so overnight/tests
# never perform real side effects; default just records intent.
Executor = Callable[["Initiative"], str]


def _noop_executor(init: Initiative) -> str:
    logger.info(f"initiative: (noop) would act on '{init.recommendation}'")
    return f"recorded: {init.recommendation}"


@dataclass
class CycleReport:
    acted: list[tuple[Initiative, str]] = field(default_factory=list)
    queued: list[Initiative] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.acted) + len(self.queued)

    def render(self) -> str:
        lines = [
            f"# Autonomous Cycle — acted {len(self.acted)}, "
            f"queued {len(self.queued)}",
        ]
        for init, did in self.acted:
            lines.append(f"✓ [{init.policy.value}] {init.recommendation} → {did}")
        for init in self.queued:
            lines.append(
                f"⏸ [{init.policy.value}] {init.recommendation} "
                "(menunggu Wildan)"
            )
        return "\n".join(lines)


def autonomous_cycle(
    executor: Executor | None = None,
    runners: dict | None = None,
    memory_root: Path | None = None,
) -> CycleReport:
    """Scan → for each initiative, act iff policy is autonomous, else queue.

    Anything PROPOSE/STOP_ASK is held for Wildan — the GM never self-acts
    on non-autonomous policies (blueprint 8.5/8.6).
    """
    run = executor or _noop_executor
    report = CycleReport()
    for init in scan_initiatives(runners, memory_root):
        if init.policy in _AUTONOMOUS:
            try:
                did = run(init)
            except Exception as exc:  # noqa: BLE001 — a failed act is queued
                logger.warning(f"initiative: executor failed ({exc})")
                report.queued.append(init)
                continue
            report.acted.append((init, did))
        else:
            report.queued.append(init)
    return report


def render_initiatives(initiatives: list[Initiative]) -> str:
    if not initiatives:
        return "Tidak ada inisiatif baru. Portfolio stabil."
    blocks = [f"## {len(initiatives)} inisiatif", ""]
    for i, init in enumerate(initiatives, 1):
        blocks.append(f"{i}. {init.render()}")
    return "\n".join(blocks)
