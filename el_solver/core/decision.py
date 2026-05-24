"""
Decision Card — DecisionCard dataclass + decide() logic untuk Round 1.

Mode: subscription via CLI subprocess. Cost = estimated, bukan actual billing.
Overflow action: alert only, tidak hard-kill.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Literal

from el_solver.core.orchestrator import Mode
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

APPROVAL_TIMEOUT_HOURS = 24


@dataclass
class DecisionCard:
    # Identity
    task_id: str
    agent: str
    signature: str
    timestamp: datetime

    # Confidence
    confidence: float
    confidence_signals: dict
    uncertainty_signals: list[str]

    # Cost (ESTIMATED — subscription mode, bukan actual billing)
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    budget_remaining_usd: float
    budget_pct_consumed: float

    # Risk
    risk_tier: int
    side_effects: list[str]
    reversibility: bool

    # Decision (diisi oleh decide())
    decision: Literal["auto", "notify", "approval_required", "reject"]
    decision_reasons: list[str]
    approval_expires_at: datetime | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["approval_expires_at"] = self.approval_expires_at.isoformat() if self.approval_expires_at else None
        return d

    @classmethod
    def from_row(cls, row: dict) -> "DecisionCard":
        return cls(
            task_id=row["task_id"],
            agent=row["agent"],
            signature=row["signature"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            confidence=row["confidence"],
            confidence_signals=json.loads(row["confidence_signals"] or "{}"),
            uncertainty_signals=json.loads(row["uncertainty_signals"] or "[]"),
            estimated_input_tokens=row["estimated_input_tokens"] or 0,
            estimated_output_tokens=row["estimated_output_tokens"] or 0,
            estimated_cost_usd=row["estimated_cost_usd"] or 0.0,
            budget_remaining_usd=row["budget_remaining_usd"] or 0.0,
            budget_pct_consumed=row["budget_pct_consumed"] or 0.0,
            risk_tier=row["risk_tier"] or 1,
            side_effects=json.loads(row["side_effects"] or "[]"),
            reversibility=bool(row["reversibility"]),
            decision=row["decision"],
            decision_reasons=json.loads(row["decision_reasons"] or "[]"),
            approval_expires_at=datetime.fromisoformat(row["approval_expires_at"]) if row.get("approval_expires_at") else None,
        )


def decide(card: DecisionCard, agent_daily_budget_remaining: float, approval_rules: list[str]) -> tuple[str, list[str]]:
    """
    Evaluasi DecisionCard dan return (decision, reasons).

    Hierarchy:
      reject          — hard blocker
      approval_required — perlu Wildan approve
      notify          — jalan tapi alert
      auto            — jalan tanpa noise
    """
    # Hard blocker: budget habis
    if card.estimated_cost_usd > agent_daily_budget_remaining and agent_daily_budget_remaining >= 0:
        return "reject", ["budget_exceeded"]

    # Approval required
    if card.risk_tier >= 4:
        return "approval_required", ["high_risk_external_action"]
    if card.confidence < 0.5:
        return "approval_required", ["low_confidence"]
    if card.budget_pct_consumed > 0.5:
        return "approval_required", ["expensive_relative_to_budget"]
    if any(action in approval_rules for action in card.side_effects):
        return "approval_required", ["action_in_approval_list"]

    # Notify
    if card.risk_tier == 3:
        return "notify", ["medium_risk_logged"]
    if 0.5 <= card.confidence < 0.8:
        return "notify", ["medium_confidence"]

    return "auto", ["all_checks_passed"]


def _task_has_specific_entities(task_message: str) -> bool:
    return bool(re.search(r"(@[\w-]+|https?://|\b\d{2,}\b|\b[A-Z][a-z0-9]+\b)", task_message))


def derive_uncertainty_signals(
    intent_mode: str | None,
    confidence: float,
    task_message: str,
    candidate_count: int = 0,
) -> list[str]:
    """Derive uncertainty signals that can trigger clarification."""
    signals: list[str] = []
    mode_value = intent_mode.value if isinstance(intent_mode, Mode) else intent_mode

    if confidence < 0.6 and mode_value == Mode.CONVERSATION.value:
        signals.append("ambiguous_objective")

    if candidate_count > 1:
        signals.append("multiple_candidates")

    word_count = len(task_message.split())
    if word_count > 100 and not _task_has_specific_entities(task_message):
        signals.append("unscoped_task")

    return signals


def build_decision_card(
    agent: str,
    task_message: str,
    confidence: float,
    confidence_signals: dict,
    risk_tier: int,
    side_effects: list[str],
    reversibility: bool,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    budget_remaining_usd: float,
    daily_budget_usd: float,
    run_id: str | None = None,
    intent_mode: str | None = None,
    candidate_count: int = 0,
) -> DecisionCard:
    """Factory: buat DecisionCard sebelum eksekusi."""
    # Reference pricing Claude Sonnet (subscription mode: estimated, bukan actual)
    INPUT_PRICE_PER_TOKEN = 3.0 / 1_000_000
    OUTPUT_PRICE_PER_TOKEN = 15.0 / 1_000_000
    estimated_cost = (
        estimated_input_tokens * INPUT_PRICE_PER_TOKEN
        + estimated_output_tokens * OUTPUT_PRICE_PER_TOKEN
    )

    budget_pct = estimated_cost / daily_budget_usd if daily_budget_usd > 0 else 0.0

    uncertainty: list[str] = []
    uncertainty.extend(
        derive_uncertainty_signals(
            intent_mode=intent_mode,
            confidence=confidence,
            task_message=task_message,
            candidate_count=candidate_count,
        )
    )
    if confidence < 0.6:
        uncertainty.append("low_history_confidence")
    if risk_tier >= 4:
        uncertainty.append("external_action")

    task_id = str(uuid.uuid4())
    signature = f"invoke.{agent}"

    card = DecisionCard(
        task_id=task_id,
        agent=agent,
        signature=signature,
        timestamp=datetime.now(timezone.utc),
        confidence=confidence,
        confidence_signals=confidence_signals,
        uncertainty_signals=uncertainty,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_cost_usd=estimated_cost,
        budget_remaining_usd=budget_remaining_usd,
        budget_pct_consumed=budget_pct,
        risk_tier=risk_tier,
        side_effects=side_effects,
        reversibility=reversibility,
        decision="auto",
        decision_reasons=[],
        approval_expires_at=None,
    )
    return card


def save_decision(card: DecisionCard, run_id: str | None = None) -> None:
    """Persist DecisionCard ke tabel decisions."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO decisions
               (task_id, run_id, agent, signature, timestamp, confidence,
                confidence_signals, uncertainty_signals,
                estimated_input_tokens, estimated_output_tokens, estimated_cost_usd,
                budget_remaining_usd, budget_pct_consumed,
                risk_tier, side_effects, reversibility,
                decision, decision_reasons, approval_expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                card.task_id,
                run_id,
                card.agent,
                card.signature,
                card.timestamp.isoformat(),
                card.confidence,
                json.dumps(card.confidence_signals),
                json.dumps(card.uncertainty_signals),
                card.estimated_input_tokens,
                card.estimated_output_tokens,
                card.estimated_cost_usd,
                card.budget_remaining_usd,
                card.budget_pct_consumed,
                card.risk_tier,
                json.dumps(card.side_effects),
                int(card.reversibility),
                card.decision,
                json.dumps(card.decision_reasons),
                card.approval_expires_at.isoformat() if card.approval_expires_at else None,
            ),
        )
        conn.commit()
        logger.debug(f"decision saved: task_id={card.task_id} decision={card.decision}")
    except Exception as e:
        logger.warning(f"save_decision failed (non-critical): {e}")
    finally:
        conn.close()


def get_decision_for_run(run_id: str) -> DecisionCard | None:
    """Ambil DecisionCard yang terkait dengan run_id."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM decisions WHERE run_id=? ORDER BY timestamp DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return DecisionCard.from_row(dict(row))
    except Exception:
        return None
    finally:
        conn.close()


def get_recent_decisions_for_agent(agent: str, limit: int = 10) -> list[DecisionCard]:
    """Ambil N DecisionCard terbaru untuk agent tertentu."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE agent=? ORDER BY timestamp DESC LIMIT ?",
            (agent, limit),
        ).fetchall()
        return [DecisionCard.from_row(dict(r)) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()
