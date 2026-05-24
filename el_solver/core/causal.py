"""Causal model reader + advisor (R14 M5).

Parses ``memory/business/causal-model.md`` (the action→KPI model) and turns
it into a *decision input*: given a metric or action keyword, return the
recorded prior (expected effect + confidence tier). This is the first
causal entry actually influencing a decision surface — it is surfaced in
the weekly report and queryable via ``el causal advise``.

Anti-superstition (blueprint 9.x): an entry only counts as ``confirmed``
once it has ≥ 3 observations; until then its prior is advisory only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from el_solver.config import settings
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

CONFIRM_THRESHOLD = 3
_FIELD_RE = re.compile(r"^-\s*(\w+):\s*(.+?)\s*$")


@dataclass
class CausalEntry:
    id: str
    action: str = ""
    metric: str = ""
    expected_effect: str = ""
    status: str = "hypothesis"
    observations: int = 0
    evidence: str = ""

    @property
    def is_confirmed(self) -> bool:
        return self.status == "confirmed" and self.observations >= CONFIRM_THRESHOLD

    def confidence_tier(self) -> str:
        if self.is_confirmed:
            return "confirmed"
        if self.observations >= CONFIRM_THRESHOLD:
            return "promotable"
        return "hypothesis"


def _model_path(root: Path | None = None) -> Path:
    base = root or settings.memory_path
    return base / "business" / "causal-model.md"


def parse_causal_model(root: Path | None = None) -> list[CausalEntry]:
    """Parse the ``### <id>`` blocks of causal-model.md."""
    path = _model_path(root)
    if not path.exists():
        return []
    entries: list[CausalEntry] = []
    current: CausalEntry | None = None
    in_entries = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            # only blocks under the "## Entri" section are real entries
            in_entries = line.strip().lower().endswith("entri")
            continue
        if line.startswith("### "):
            if current is not None:
                entries.append(current)
            current = CausalEntry(id=line[4:].strip()) if in_entries else None
            continue
        if current is None:
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if key == "action":
            current.action = val
        elif key == "metric":
            current.metric = val.strip("`")
        elif key == "expected_effect":
            current.expected_effect = val
        elif key == "status":
            current.status = val
        elif key == "observations":
            try:
                current.observations = int(val)
            except ValueError:
                current.observations = 0
        elif key == "evidence":
            current.evidence = val
    if current is not None:
        entries.append(current)
    return entries


def advise(query: str, root: Path | None = None) -> list[CausalEntry]:
    """Causal priors relevant to a metric/action query (substring match)."""
    q = (query or "").lower().strip()
    if not q:
        return []
    out = [
        e
        for e in parse_causal_model(root)
        if q in e.metric.lower() or q in e.action.lower() or q in e.id.lower()
    ]
    # confirmed priors first, then by observation count
    out.sort(key=lambda e: (e.is_confirmed, e.observations), reverse=True)
    return out


def report_section(root: Path | None = None) -> list[str]:
    """Lines for the weekly report so causal priors influence decisions."""
    entries = parse_causal_model(root)
    if not entries:
        return ["_(causal model kosong)_"]
    lines: list[str] = []
    for e in entries:
        lines.append(
            f"- **{e.id}** [{e.confidence_tier()}, n={e.observations}]: "
            f"{e.action} → `{e.metric}` ({e.expected_effect})"
        )
    return lines
