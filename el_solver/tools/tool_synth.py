"""Tool synthesis from recurring workflows (R16 M4, blueprint 9.8).

When the same manual workflow recurs >= THRESHOLD times, draft a tool
spec + an implementation stub and write it to ``el_solver/skills/proposed/``
as a *proposal only*. It is never auto-registered — proposed/ is
gitignored and Wildan-reviewed (consistent with the existing skill-
proposal gate). Deterministic, no LLM.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

RECUR_THRESHOLD = 5
PROPOSED_DIR = PROJECT_ROOT / "el_solver" / "skills" / "proposed"


def _signature(workflow: str) -> str:
    """Normalize a workflow description to a recurrence key."""
    text = re.sub(r"[^\w\s]", " ", workflow.lower())
    return " ".join(sorted(set(text.split())))


def detect_recurring(
    workflows: list[str], threshold: int = RECUR_THRESHOLD
) -> list[tuple[str, int, str]]:
    """Return (signature, count, sample) for workflows seen >= threshold."""
    by_sig: dict[str, list[str]] = {}
    for w in workflows:
        by_sig.setdefault(_signature(w), []).append(w)
    counts = Counter({sig: len(v) for sig, v in by_sig.items()})
    out: list[tuple[str, int, str]] = []
    for sig, n in counts.most_common():
        if n >= threshold:
            out.append((sig, n, by_sig[sig][0]))
    return out


@dataclass
class ToolSpec:
    name: str
    description: str
    inputs: list[str] = field(default_factory=lambda: ["text"])
    outputs: list[str] = field(default_factory=lambda: ["result"])
    preconditions: list[str] = field(default_factory=list)
    success_indicator: str = "output non-empty and matches expected shape"
    occurrences: int = 0

    def draft_impl(self) -> str:
        return (
            f'"""Proposed tool: {self.name} (auto-drafted, NOT registered)."""\n'
            f"def {self.name}(text: str) -> str:\n"
            f"    # TODO(wildan): implement — recurred {self.occurrences}x\n"
            f"    raise NotImplementedError\n"
        )


def _slug(text: str) -> str:
    return re.sub(r"[\s_]+", "_", re.sub(r"[^\w\s]", "", text.lower())).strip(
        "_"
    )[:40] or "tool"


def synth_tool_spec(signature: str, sample: str, occurrences: int) -> ToolSpec:
    name = "synth_" + _slug(sample)
    return ToolSpec(
        name=name,
        description=f"Auto-drafted from recurring workflow: {sample[:80]}",
        occurrences=occurrences,
    )


def write_proposal(spec: ToolSpec, proposed_dir: Path | None = None) -> Path:
    """Write spec + stub as a proposal (Wildan-gated, not auto-deployed)."""
    base = proposed_dir or PROPOSED_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{spec.name}.proposal.md"
    safe_desc = spec.description.replace("\n", " ").replace('"', "'")
    body = [
        "---",
        f"name: {spec.name}",
        f'description: "{safe_desc}"',
        "type: tool-proposal",
        "status: pending-wildan-review",
        "---",
        "",
        f"# Tool Proposal: {spec.name}",
        "",
        f"- occurrences: {spec.occurrences}",
        f"- inputs: {json.dumps(spec.inputs)}",
        f"- outputs: {json.dumps(spec.outputs)}",
        f"- success_indicator: {spec.success_indicator}",
        "",
        "## Draft implementation",
        "```python",
        spec.draft_impl().rstrip(),
        "```",
        "",
        "_Proposal only. NOT registered. Review & implement before use._",
        "",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    logger.info(f"tool_synth: wrote proposal {path.name}")
    return path


def synthesize_from_workflows(
    workflows: list[str],
    threshold: int = RECUR_THRESHOLD,
    proposed_dir: Path | None = None,
) -> list[Path]:
    """End-to-end: detect recurring → spec → proposal files."""
    paths: list[Path] = []
    for sig, count, sample in detect_recurring(workflows, threshold):
        spec = synth_tool_spec(sig, sample, count)
        paths.append(write_proposal(spec, proposed_dir))
    return paths
