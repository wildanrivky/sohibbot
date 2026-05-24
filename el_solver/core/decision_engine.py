"""Executive decision engine (R15 M1).

Codifies the blueprint's decision matrix (Sections 8.1–8.3): every action
is scored on stakes × reversibility × confidence and routed to one of four
policies. Every decision is appended to an immutable audit log
(``data/audit/{YYYY-MM-DD}.jsonl``) so Wildan can see *what* El Solver did
and *why* (blueprint 7.5).

This composes — it does not replace — the existing ``core/risk.py``
(agent-spec L0–L3) and ``core/decision.py`` (decision cards). It adds the
numeric ``risk_score = severity × probability × irreversibility`` and the
confidence×risk routing grid those modules don't cover.
"""
from __future__ import annotations

import importlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

AUDIT_DIR = PROJECT_ROOT / "data" / "audit"


class Policy(StrEnum):
    ACT_LOG = "act_log"            # do it, log only
    ACT_NOTIFY = "act_notify"      # do it, notify Wildan after
    PROPOSE_OPTIONS = "propose"    # propose N ranked options, wait
    STOP_ASK = "stop_ask"          # stop, require explicit approval


class Stakes(StrEnum):
    LOW = "low"
    HIGH = "high"


# risk_score thresholds (blueprint 8.2)
_RISK_AUTO = 5.0
_RISK_NOTIFY = 15.0
_RISK_PROPOSE = 40.0


def risk_score(severity: int, probability: float, irreversibility: int) -> float:
    """severity(1–5) × probability(0–1) × irreversibility(1–5)."""
    sev = max(1, min(5, int(severity)))
    prob = max(0.0, min(1.0, float(probability)))
    irr = max(1, min(5, int(irreversibility)))
    return round(sev * prob * irr, 4)


def _risk_band(score: float) -> str:
    if score < _RISK_AUTO:
        return "low"
    if score < _RISK_NOTIFY:
        return "med"
    if score < _RISK_PROPOSE:
        return "high"
    return "critical"


def _conf_band(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.6:
        return "med"
    return "low"


# Confidence × risk routing grid (blueprint 8.3)
_GRID: dict[tuple[str, str], Policy] = {
    ("high", "low"): Policy.ACT_LOG,
    ("high", "med"): Policy.ACT_LOG,
    ("high", "high"): Policy.ACT_NOTIFY,
    ("high", "critical"): Policy.PROPOSE_OPTIONS,
    ("med", "low"): Policy.ACT_LOG,
    ("med", "med"): Policy.ACT_NOTIFY,
    ("med", "high"): Policy.PROPOSE_OPTIONS,
    ("med", "critical"): Policy.STOP_ASK,
    ("low", "low"): Policy.ACT_NOTIFY,
    ("low", "med"): Policy.PROPOSE_OPTIONS,
    ("low", "high"): Policy.STOP_ASK,
    ("low", "critical"): Policy.STOP_ASK,
}


@dataclass
class DecisionInput:
    action: str
    severity: int = 1            # 1 cosmetic … 5 business-threatening
    probability: float = 0.0     # 0..1 chance of the bad outcome
    irreversibility: int = 1     # 1 trivial undo … 5 permanent
    confidence: float = 1.0      # 0..1
    stakes: Stakes = Stakes.LOW
    reversible: bool = True
    context: dict[str, object] = field(default_factory=dict)


@dataclass
class DecisionOutcome:
    decision_id: str
    action: str
    policy: Policy
    risk_score: float
    risk_band: str
    confidence_band: str
    rationale: str
    guardrail_block: bool = False

    @property
    def autonomous(self) -> bool:
        """True if El Solver may act without waiting for Wildan."""
        return self.policy in (Policy.ACT_LOG, Policy.ACT_NOTIFY)


def decide(inp: DecisionInput) -> DecisionOutcome:
    """Route an action to a policy and write an audit record.

    Order: ethical guardrails (hard block → STOP_ASK) → numeric risk →
    confidence×risk grid → stakes/reversibility safety floor.
    """
    score = risk_score(inp.severity, inp.probability, inp.irreversibility)
    rband = _risk_band(score)
    cband = _conf_band(inp.confidence)

    guardrail_block = False
    guardrail_reason = ""
    try:
        guardrails = importlib.import_module("el_solver.core.ethical_guardrails")
        verdict = guardrails.check_action(inp.action, inp.context)
        if not verdict.allowed:
            guardrail_block = True
            guardrail_reason = verdict.reason
    except ModuleNotFoundError:
        pass  # guardrails land in M2
    except Exception as exc:  # noqa: BLE001 — never let guardrail crash a decision
        logger.warning(f"decision_engine: guardrail check failed ({exc})")

    if guardrail_block:
        policy = Policy.STOP_ASK
        rationale = f"ethical guardrail: {guardrail_reason}"
    else:
        policy = _GRID.get((cband, rband), Policy.STOP_ASK)
        # Safety floor (blueprint 8.1): irreversible + high stakes is never
        # fully autonomous regardless of grid optimism.
        if (
            not inp.reversible
            and inp.stakes is Stakes.HIGH
            and policy in (Policy.ACT_LOG, Policy.ACT_NOTIFY)
        ):
            policy = Policy.PROPOSE_OPTIONS
        rationale = (
            f"risk_score={score} ({rband}), confidence={inp.confidence:.2f} "
            f"({cband}), stakes={inp.stakes.value}, "
            f"reversible={inp.reversible} → {policy.value}"
        )

    outcome = DecisionOutcome(
        decision_id=str(uuid.uuid4()),
        action=inp.action,
        policy=policy,
        risk_score=score,
        risk_band=rband,
        confidence_band=cband,
        rationale=rationale,
        guardrail_block=guardrail_block,
    )
    audit_log(inp, outcome)
    return outcome


def audit_log(
    inp: DecisionInput,
    outcome: DecisionOutcome,
    audit_dir: Path | None = None,
) -> Path:
    """Append an immutable JSONL audit record. Returns the file path."""
    base = audit_dir or AUDIT_DIR
    base.mkdir(parents=True, exist_ok=True)
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = base / f"{date}.jsonl"
    input_dict: dict[str, object] = asdict(inp)
    input_dict["stakes"] = inp.stakes.value  # enum → str for JSON
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "decision_id": outcome.decision_id,
        "input": input_dict,
        "outcome": {
            "policy": outcome.policy.value,
            "risk_score": outcome.risk_score,
            "risk_band": outcome.risk_band,
            "confidence_band": outcome.confidence_band,
            "rationale": outcome.rationale,
            "guardrail_block": outcome.guardrail_block,
        },
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:
        logger.error(f"decision_engine: audit write failed: {exc}")
    return path


def retrospective(days: int = 30, audit_dir: Path | None = None) -> dict:
    """Aggregate the audit log over the last ``days`` days (blueprint M5).

    Returns policy counts, guardrail-block count, and the autonomous rate
    (ACT_* / total) — the numbers for Wildan's decision-log retrospective.
    """
    from datetime import timedelta

    base = audit_dir or AUDIT_DIR
    today = datetime.now(UTC).date()
    policy_counts: dict[str, int] = {p.value: 0 for p in Policy}
    guardrail_blocks = 0
    total = 0
    for i in range(days):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for rec in read_audit(d, base):
            total += 1
            pol = rec.get("outcome", {}).get("policy", "")
            if pol in policy_counts:
                policy_counts[pol] += 1
            if rec.get("outcome", {}).get("guardrail_block"):
                guardrail_blocks += 1
    autonomous = policy_counts[Policy.ACT_LOG.value] + policy_counts[
        Policy.ACT_NOTIFY.value
    ]
    return {
        "days": days,
        "total": total,
        "policy_counts": policy_counts,
        "guardrail_blocks": guardrail_blocks,
        "autonomous_rate": round(autonomous / total, 4) if total else 0.0,
    }


def read_audit(date: str | None = None, audit_dir: Path | None = None) -> list[dict]:
    """Read audit records for a date (default: today). Empty if none."""
    base = audit_dir or AUDIT_DIR
    date = date or datetime.now(UTC).strftime("%Y-%m-%d")
    path = base / f"{date}.jsonl"
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
