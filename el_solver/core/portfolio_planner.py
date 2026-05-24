"""Weekly portfolio review (R17 M1).

El Solver-initiated review of Wildan's portfolio: reads
``business/portfolio.md`` + ``active-okrs.md``, the latest KPI snapshot
per declared metric, and the causal priors, then produces a structured
review with a per-unit status. Deterministic, LLM-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from el_solver.config import settings
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_METRIC_RE = re.compile(r"`([a-z0-9_-]+\.[a-z0-9_-]+)`")


@dataclass
class UnitReview:
    metric: str
    unit: str
    latest_value: float | None
    latest_ts: str | None
    status: str  # "data" | "no-data"


@dataclass
class PortfolioReview:
    generated_at: str
    units: list[UnitReview] = field(default_factory=list)
    okr_present: bool = False
    causal_lines: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            f"# Weekly Portfolio Review — {self.generated_at}",
            "",
            "## Units (latest KPI per declared metric)",
        ]
        if not self.units:
            lines.append("_(portfolio.md belum punya metrik)_")
        for u in self.units:
            if u.status == "data":
                lines.append(
                    f"- `{u.metric}` ({u.unit}): **{u.latest_value}** "
                    f"({(u.latest_ts or '')[:10]})"
                )
            else:
                lines.append(f"- `{u.metric}` ({u.unit}): _(belum ada data)_")
        lines += [
            "",
            f"## OKR: {'ada' if self.okr_present else 'TIDAK ADA'}",
            "",
            "## Causal priors",
        ]
        lines += self.causal_lines or ["_(kosong)_"]
        return "\n".join(lines)


def _declared_metrics(memory_root: Path) -> list[str]:
    p = memory_root / "business" / "portfolio.md"
    if not p.exists():
        return []
    seen: list[str] = []
    for m in _METRIC_RE.findall(p.read_text(encoding="utf-8")):
        if m not in seen:
            seen.append(m)
    return seen


def weekly_portfolio_review(
    memory_root: Path | None = None,
) -> PortfolioReview:
    base = memory_root or settings.memory_path
    metrics = _declared_metrics(base)

    units: list[UnitReview] = []
    try:
        from el_solver.core.kpi_ingest import latest, unit_of

        for metric in metrics:
            pt = latest(metric)
            if pt is None:
                units.append(
                    UnitReview(metric, unit_of(metric), None, None, "no-data")
                )
            else:
                units.append(
                    UnitReview(metric, pt.unit or unit_of(metric),
                               pt.value, pt.ts, "data")
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"portfolio_planner: kpi unavailable ({exc})")

    okr_path = base / "business" / "active-okrs.md"
    okr_present = okr_path.exists() and bool(
        okr_path.read_text(encoding="utf-8").strip()
    )

    causal_lines: list[str] = []
    try:
        from el_solver.core.causal import report_section

        causal_lines = report_section(base)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"portfolio_planner: causal unavailable ({exc})")

    return PortfolioReview(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        units=units,
        okr_present=okr_present,
        causal_lines=causal_lines,
    )
