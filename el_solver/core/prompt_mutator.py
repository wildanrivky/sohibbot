"""Prompt mutation + A/B against golden sets (R16 M3).

Blueprint 9.6: for low-performing agents, generate prompt variants, A/B
them on the held-out golden set, promote a winner — but only with a
champion kept by default and Wildan notified *before* any live swap.

Two safety choices for autonomous overnight use:

1. **Deterministic, pluggable mutator.** Default `rule_mutator` appends
   clarifying directives — a real but LLM-free strategy. A future round
   injects an LLM mutator via the `mutator=` arg; nothing here blocks on
   a model call.
2. **No silent swap.** `ab_run` records a *recommendation* (`promoted=1`)
   and returns a notify message. It never rewrites an agent's prompt
   file — that stays a Wildan-gated action.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from el_solver.core.self_eval import Runner, score_agent
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

# mutator(base_prompt) -> list of candidate prompts (excluding base)
Mutator = Callable[[str], list[str]]
# runner_factory(prompt) -> a Runner bound to that prompt
RunnerFactory = Callable[[str], Runner]

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS ab_runs (
  id TEXT PRIMARY KEY,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  agent TEXT NOT NULL,
  base_score REAL NOT NULL,
  winner_index INTEGER NOT NULL,
  winner_score REAL NOT NULL,
  promoted INTEGER DEFAULT 0,
  variants TEXT
);
"""

# Minimum golden cases before a promotion may be *recommended*. Seed golden
# sets are tiny; real significance (blueprint: n>=30) needs Wildan-sized sets.
MIN_CASES_FOR_PROMOTION = 5
MIN_IMPROVEMENT = 0.05


@dataclass
class ABResult:
    agent: str
    base_score: float
    scores: list[float]
    winner_index: int  # 0 = champion/base kept
    winner_score: float
    promoted: bool
    variants: list[str] = field(default_factory=list)

    def notify(self) -> str:
        if not self.promoted:
            return (
                f"A/B {self.agent}: champion dipertahankan "
                f"(base={self.base_score:.0%}, best={self.winner_score:.0%}). "
                "Tidak ada perubahan."
            )
        return (
            f"⚠️ A/B {self.agent}: kandidat #{self.winner_index} mengalahkan "
            f"champion ({self.base_score:.0%} → {self.winner_score:.0%}). "
            "REKOMENDASI swap — menunggu approval Wildan sebelum go-live."
        )


def rule_mutator(base: str) -> list[str]:
    """Deterministic mutation strategy (no LLM)."""
    directives = [
        "\n\nJawab ringkas dan langsung ke poin.",
        "\n\nPastikan output mengikuti format yang diminta persis.",
        "\n\nSertakan hanya informasi yang terverifikasi; jangan mengarang.",
    ]
    return [base + d for d in directives]


def ab_run(
    agent: str,
    base_prompt: str,
    runner_factory: RunnerFactory,
    mutator: Mutator | None = None,
    root: Path | None = None,
    db_path: Path | None = None,
) -> ABResult:
    """Score base + mutated prompts on the golden set; champion kept by default.

    A candidate is only *recommended* (`promoted=True`) if it beats the
    champion by >= MIN_IMPROVEMENT and the golden set has enough cases.
    """
    mut = mutator or rule_mutator
    candidates = mut(base_prompt)
    variants = [base_prompt, *candidates]

    scores: list[float] = []
    total_cases = 0
    for v in variants:
        s = score_agent(agent, runner_factory(v), root)
        total_cases = s.total
        scores.append(s.pass_rate)

    base_score = scores[0]
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    best_score = scores[best_idx]

    promoted = (
        best_idx != 0
        and total_cases >= MIN_CASES_FOR_PROMOTION
        and (best_score - base_score) >= MIN_IMPROVEMENT
    )
    winner_index = best_idx if promoted else 0
    winner_score = best_score if promoted else base_score

    result = ABResult(
        agent=agent,
        base_score=base_score,
        scores=scores,
        winner_index=winner_index,
        winner_score=winner_score,
        promoted=promoted,
        variants=variants,
    )
    _record(result, db_path)
    return result


def _record(result: ABResult, db_path: Path | None = None) -> str:
    conn = get_connection(db_path)
    try:
        conn.executescript(_ENSURE_SQL)
        row_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO ab_runs
               (id, agent, base_score, winner_index, winner_score,
                promoted, variants)
               VALUES (?,?,?,?,?,?,?)""",
            (
                row_id,
                result.agent,
                result.base_score,
                result.winner_index,
                result.winner_score,
                1 if result.promoted else 0,
                json.dumps(result.variants, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return row_id


def ab_history(
    agent: str | None = None,
    limit: int = 50,
    db_path: Path | None = None,
) -> list[dict]:
    conn = get_connection(db_path)
    try:
        conn.executescript(_ENSURE_SQL)
        where = "WHERE agent=?" if agent else ""
        params: tuple = (agent, limit) if agent else (limit,)
        rows = conn.execute(
            f"SELECT id, ts, agent, base_score, winner_index, winner_score, "
            f"promoted FROM ab_runs {where} ORDER BY ts DESC, rowid DESC "
            f"LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
