"""Escalation surfaces: PROPOSE N OPTIONS + STOP-AND-ASK (R15 M3).

When `decision_engine` returns ``PROPOSE_OPTIONS`` or ``STOP_ASK`` the GM
must talk to Wildan in a fixed, scannable shape (blueprint 8 / 14.3:
WHAT / WHY / options ranked with rationale / recommended). This module
builds those messages and writes a durable decision record to
``memory/decisions/`` so nothing relies on Wildan's memory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from el_solver.config import settings
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Option:
    label: str
    detail: str = ""
    score: float = 0.0          # 0..1 desirability
    risk_note: str = ""
    rank: int = 0               # filled by rank_options
    recommended: bool = False


def rank_options(options: list[Option]) -> list[Option]:
    """Sort by score desc, assign 1-based rank, flag the top as recommended."""
    ordered = sorted(options, key=lambda o: o.score, reverse=True)
    for i, opt in enumerate(ordered, start=1):
        opt.rank = i
        opt.recommended = i == 1
    return ordered


def render_proposal(
    action: str,
    options: list[Option],
    rationale: str = "",
) -> str:
    """Markdown PROPOSE-N-OPTIONS message with ranked alternatives."""
    ranked = rank_options(options)
    lines = [
        "PROPOSE — butuh keputusan Wildan",
        f"WHAT: {action}",
    ]
    if rationale:
        lines.append(f"WHY: {rationale}")
    lines.append("OPTIONS (ranked):")
    for opt in ranked:
        tag = " ⟵ rekomendasi" if opt.recommended else ""
        lines.append(f"  {opt.rank}. {opt.label}{tag}")
        if opt.detail:
            lines.append(f"     {opt.detail}")
        if opt.risk_note:
            lines.append(f"     risiko: {opt.risk_note}")
    lines.append("Balas dengan nomor opsi untuk lanjut.")
    return "\n".join(lines)


def render_stop_ask(action: str, reason: str) -> str:
    """Markdown STOP-AND-ASK message (irreversible / guardrail / low-conf)."""
    return "\n".join(
        [
            "STOP — perlu approval eksplisit Wildan",
            f"WHAT: {action}",
            f"WHY: {reason}",
            "Tidak ada aksi diambil. Tunggu instruksi Wildan.",
        ]
    )


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", text)[:40].strip("-") or "decision"


def write_decision_record(
    action: str,
    policy: str,
    rationale: str,
    options: list[Option] | None = None,
    *,
    decision_id: str = "",
    memory_root: Path | None = None,
) -> Path:
    """Persist a decision to memory/decisions/{date}-{slug}.md (status: pending)."""
    base = (memory_root or settings.memory_path) / "decisions"
    base.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    slug = _slugify(action)
    path = base / f"{date}-{slug}.md"

    opt_lines: list[str] = []
    for opt in rank_options(options or []):
        flag = " (rekomendasi)" if opt.recommended else ""
        opt_lines.append(
            f"{opt.rank}. **{opt.label}**{flag} — {opt.detail or '-'}"
            + (f" [risiko: {opt.risk_note}]" if opt.risk_note else "")
        )

    body = [
        "---",
        f"name: decision-{slug}",
        f"description: Keputusan '{action[:60]}' — {policy}",
        "type: decision",
        "---",
        "",
        f"# Decision: {action}",
        "",
        f"- decision_id: {decision_id or '-'}",
        f"- policy: {policy}",
        "- status: pending",
        f"- created: {datetime.now(UTC).isoformat()}",
        "",
        "## Rationale",
        rationale or "-",
    ]
    if opt_lines:
        body += ["", "## Options"] + opt_lines
    body += ["", "## Outcome", "_(menunggu keputusan Wildan)_", ""]

    path.write_text("\n".join(body), encoding="utf-8")
    logger.info(f"proposal: wrote decision record {path.name}")
    return path
