"""Self-evaluation against golden test sets (R16 M1/M2).

Golden sets live in ``data/golden/{agent}/cases.json`` and are
Wildan-curated (blueprint 9.6 / 16: held-out, not auto-generated, so
mutations can't game them). A case passes when the agent output contains
all ``expect_contains`` substrings and (optionally) the status matches.

Scoring is deterministic and LLM-free: a ``runner`` callable is injected
so this works offline and in tests. Real runs pass the agent's actual
runner; the default runner fails every case (safe default).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

GOLDEN_ROOT = PROJECT_ROOT / "data" / "golden"

# runner(input_text) -> (output_text, status)
Runner = Callable[[str], tuple[str, str]]

LOW_PERFORMER_THRESHOLD = 0.7  # blueprint 9.6

# Lifecycle marker for machine-seeded draft cases. They are NOT scored
# until Wildan reviews them and removes the marker (golden sets must stay
# Wildan-curated so mutations can't game them — blueprint 9.6).
DRAFT_STATUS = "draft-needs-wildan-review"


@dataclass
class GoldenCase:
    id: str
    input: str
    expect_contains: list[str] = field(default_factory=list)
    expect_status: str = "completed"
    anti_pattern: list[str] = field(default_factory=list)


@dataclass
class AgentScore:
    agent: str
    total: int
    passed: int
    failures: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    @property
    def is_low_performer(self) -> bool:
        return self.total > 0 and self.pass_rate < LOW_PERFORMER_THRESHOLD


def _golden_root(root: Path | None = None) -> Path:
    return root or GOLDEN_ROOT


def list_golden_agents(root: Path | None = None) -> list[str]:
    base = _golden_root(root)
    if not base.is_dir():
        return []
    return sorted(
        d.name for d in base.iterdir() if d.is_dir() and (d / "cases.json").exists()
    )


def load_golden(agent: str, root: Path | None = None) -> list[GoldenCase]:
    path = _golden_root(root) / agent / "cases.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"self_eval: gagal baca golden {agent}: {exc}")
        return []
    out: list[GoldenCase] = []
    for c in data.get("cases", []):
        # Skip draft cases until Wildan promotes them (removes the marker).
        if str(c.get("status") or "") == DRAFT_STATUS:
            continue
        out.append(
            GoldenCase(
                id=str(c.get("id") or ""),
                input=str(c.get("input") or ""),
                expect_contains=[str(s) for s in c.get("expect_contains", [])],
                expect_status=str(c.get("expect_status") or "completed"),
                anti_pattern=[str(s) for s in c.get("anti_pattern", [])],
            )
        )
    return out


def score_case(case: GoldenCase, output: str, status: str) -> bool:
    if case.expect_status and status != case.expect_status:
        return False
    low = (output or "").lower()
    if any(bad.lower() in low for bad in case.anti_pattern):
        return False
    return all(sub.lower() in low for sub in case.expect_contains)


def _default_runner(_input: str) -> tuple[str, str]:
    return "", "error"


def score_agent(
    agent: str,
    runner: Runner | None = None,
    root: Path | None = None,
) -> AgentScore:
    """Run every golden case through ``runner`` and score it."""
    run = runner or _default_runner
    cases = load_golden(agent, root)
    passed = 0
    failures: list[str] = []
    for case in cases:
        try:
            output, status = run(case.input)
        except Exception as exc:  # noqa: BLE001 — a crash is a failed case
            output, status = str(exc), "error"
        if score_case(case, output, status):
            passed += 1
        else:
            failures.append(case.id)
    return AgentScore(agent=agent, total=len(cases), passed=passed, failures=failures)


def score_all(
    runners: dict[str, Runner] | None = None,
    root: Path | None = None,
) -> list[AgentScore]:
    """Score every agent that has a golden set."""
    runners = runners or {}
    return [
        score_agent(agent, runners.get(agent), root)
        for agent in list_golden_agents(root)
    ]


def flag_low_performers(
    runners: dict[str, Runner] | None = None,
    root: Path | None = None,
) -> list[str]:
    """Agents below LOW_PERFORMER_THRESHOLD on their golden set."""
    return [s.agent for s in score_all(runners, root) if s.is_low_performer]


def self_eval_report(
    runners: dict[str, Runner] | None = None,
    root: Path | None = None,
    trace_days: int = 7,
) -> str:
    """Markdown self-eval: golden pass-rate + tracer rollup + low-performer flags.

    LLM-free. Without injected runners every golden case fails (safe
    default) — the report still renders and flags the agents.
    """
    from datetime import UTC, datetime

    scores = score_all(runners, root)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Self-Eval Report — {now}", "", "## Golden Pass Rate"]
    if not scores:
        lines.append("_(belum ada golden set)_")
    else:
        lines.append("| Agent | Pass | Total | Rate | Flag |")
        lines.append("|---|---|---|---|---|")
        for s in scores:
            flag = "⚠️ LOW" if s.is_low_performer else "ok"
            lines.append(
                f"| {s.agent} | {s.passed} | {s.total} | "
                f"{s.pass_rate * 100:.0f}% | {flag} |"
            )

    lines += ["", f"## Tracer Rollup (last {trace_days}d)"]
    try:
        from el_solver.core.eval import rollup_agent

        any_trace = False
        for s in scores:
            r = rollup_agent(s.agent, trace_days)
            if r["runs"]:
                any_trace = True
                lines.append(
                    f"- {s.agent}: {r['runs']} runs, "
                    f"{r['success_rate'] * 100:.0f}% success"
                )
        if not any_trace:
            lines.append("_(belum ada trace)_")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"self_eval: rollup unavailable ({exc})")
        lines.append("_(rollup tidak tersedia)_")

    low = [s.agent for s in scores if s.is_low_performer]
    lines += ["", "## Low Performers (→ R16 M3 mutation candidates)"]
    lines.append(", ".join(low) if low else "_(tidak ada)_")
    return "\n".join(lines)


def improvement_retrospective(
    runners: dict[str, Runner] | None = None,
    root: Path | None = None,
) -> str:
    """90-day-style retrospective: golden, A/B promotions, skill gaps.

    Aggregates the R16 subsystems into one self-improvement view
    (blueprint M5). LLM-free; degrades gracefully if a subsystem is
    unavailable.
    """
    scores = score_all(runners, root)
    avg = (
        round(sum(s.pass_rate for s in scores) / len(scores), 4)
        if scores
        else 0.0
    )
    low = [s.agent for s in scores if s.is_low_performer]

    lines = [
        "# Improvement Retrospective",
        "",
        f"- agents scored: {len(scores)}",
        f"- avg golden pass-rate: {avg:.0%}",
        f"- low performers: {len(low)} ({', '.join(low) or '-'})",
    ]

    try:
        from el_solver.core.prompt_mutator import ab_history

        hist = ab_history(limit=200)
        promoted = sum(1 for h in hist if h.get("promoted"))
        lines.append(
            f"- A/B runs: {len(hist)} ({promoted} promotion recommendations)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"self_eval: ab_history unavailable ({exc})")
        lines.append("- A/B runs: _(belum ada)_")

    try:
        from el_solver.core.skill_gap import skill_gaps

        gaps = skill_gaps(runners, golden_root=root)
        lines.append(f"- open skill gaps: {len(gaps)}")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"self_eval: skill_gaps unavailable ({exc})")
        lines.append("- open skill gaps: _(n/a)_")

    return "\n".join(lines)
