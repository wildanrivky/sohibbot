"""Clarification layer for ambiguous intent and execution plans."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from el_solver.core.orchestrator import IntentResult, Mode
from el_solver.core.planner import PlanV1
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "data" / "el-solver.db"
_PENDING_TTL_MINUTES = 30


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _intent_to_json(intent: IntentResult) -> dict[str, Any]:
    return {
        "mode": intent.mode.value,
        "confidence": intent.confidence,
        "raw_message": intent.raw_message,
        "agent_name": intent.agent_name,
        "method": intent.method,
        "extras": intent.extras,
    }


def _intent_from_json(data: dict[str, Any]) -> IntentResult:
    mode_raw = data.get("mode") or Mode.CONVERSATION.value
    mode = Mode(mode_raw) if mode_raw in Mode._value2member_map_ else Mode.CONVERSATION
    return IntentResult(
        mode=mode,
        confidence=float(data.get("confidence") or 0.0),
        raw_message=str(data.get("raw_message") or ""),
        agent_name=data.get("agent_name") or None,
        method=data.get("method") or "keyword",
        extras=dict(data.get("extras") or {}),
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _is_expired(asked_at: str | None) -> bool:
    ts = _parse_timestamp(asked_at)
    if ts is None:
        return False
    return datetime.now(timezone.utc) - ts > timedelta(minutes=_PENDING_TTL_MINUTES)


def _single_line(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if cleaned.endswith("."):
        cleaned = cleaned[:-1].strip()
    words = cleaned.split()
    if len(words) > 15:
        cleaned = " ".join(words[:15]).rstrip(".,;:!?")
    return cleaned


def _fallback_question(signal: str, intent: IntentResult | None = None) -> str:
    raw = (intent.raw_message if intent else "").strip()
    if signal == "ambiguous_objective":
        return "Apa tujuan utamanya yang ingin dicapai?"
    if signal == "multiple_candidates":
        return "Agent mana yang harus saya pakai?"
    if signal == "unscoped_task":
        return "Target output spesifiknya apa?"
    if raw:
        return "Apa detail paling penting yang perlu saya tahu?"
    return "Boleh jelaskan detail yang kurang jelas?"


def _generate_question(raw_task: str, signal: str) -> str:
    from el_solver.llm import call_claude_cli

    prompt = (
        "Kamu adalah asisten klarifikasi. Buat SATU pertanyaan klarifikasi singkat dalam bahasa Indonesia.\n"
        "Aturan: maksimum 15 kata, hanya satu kalimat, tanpa salam atau penjelasan.\n\n"
        f"Task: {raw_task}\n"
        f"Confusion: {signal}\n\n"
        "Pertanyaan:"
    )
    try:
        response, *_ = call_claude_cli(prompt, timeout=30)
        question = _single_line(response)
        return question if question else _fallback_question(signal)
    except Exception as exc:
        logger.warning(f"clarifier: question generation failed ({signal}): {exc}")
        return _fallback_question(signal)


def _store_original_state(
    conn: sqlite3.Connection,
    clarification_id: str,
    user_id: str,
    channel: str,
    intent: IntentResult,
    question: str,
    context: str,
) -> None:
    conn.execute(
        "DELETE FROM pending_clarifications WHERE user_id=? AND channel=?",
        (user_id, channel),
    )
    conn.execute(
        """INSERT INTO clarifications
           (id, task_id, agent, channel, user_id, question, context, status)
           VALUES (?,?,?,?,?,?,?,'pending')""",
        (
            clarification_id,
            intent.extras.get("task_id"),
            intent.agent_name,
            channel,
            user_id,
            question,
            context,
        ),
    )
    conn.execute(
        """INSERT INTO pending_clarifications
           (user_id, channel, clarification_id, original_intent_json)
           VALUES (?,?,?,?)""",
        (
            user_id,
            channel,
            clarification_id,
            json.dumps(_intent_to_json(intent), ensure_ascii=False),
        ),
    )


def load_pending(user_id: str, channel: str) -> dict[str, Any] | None:
    """Load clarification pending untuk user/channel, dengan TTL 30 menit."""
    conn = _get_conn()
    try:
        row = conn.execute(
            """SELECT c.*, p.original_intent_json
               FROM clarifications c
               JOIN pending_clarifications p ON p.clarification_id = c.id
               WHERE p.user_id=? AND p.channel=? AND c.status='pending'
               ORDER BY c.asked_at DESC
               LIMIT 1""",
            (user_id, channel),
        ).fetchone()
        if not row:
            stale = conn.execute(
                "SELECT clarification_id FROM pending_clarifications WHERE user_id=? AND channel=?",
                (user_id, channel),
            ).fetchone()
            if stale:
                conn.execute(
                    "DELETE FROM pending_clarifications WHERE user_id=? AND channel=?",
                    (user_id, channel),
                )
                conn.commit()
            return None

        pending = dict(row)
        if _is_expired(pending.get("asked_at")):
            conn.execute(
                "UPDATE clarifications SET status='timeout' WHERE id=?",
                (pending["id"],),
            )
            conn.execute(
                "DELETE FROM pending_clarifications WHERE user_id=? AND channel=?",
                (user_id, channel),
            )
            conn.commit()
            return None
        return pending
    finally:
        conn.close()


def store_pending(
    user_id: str,
    channel: str,
    intent: IntentResult,
    question: str,
    context: str | None = None,
) -> str:
    """Persist clarification request + pending state."""
    clarification_id = str(uuid.uuid4())
    conn = _get_conn()
    try:
        previous = conn.execute(
            "SELECT clarification_id FROM pending_clarifications WHERE user_id=? AND channel=?",
            (user_id, channel),
        ).fetchone()
        if previous:
            conn.execute(
                "UPDATE clarifications SET status='timeout' WHERE id=?",
                (previous["clarification_id"],),
            )
            conn.execute(
                "DELETE FROM pending_clarifications WHERE user_id=? AND channel=?",
                (user_id, channel),
            )
        _store_original_state(
            conn,
            clarification_id=clarification_id,
            user_id=user_id,
            channel=channel,
            intent=intent,
            question=question,
            context=context or intent.raw_message,
        )
        conn.commit()
        return clarification_id
    finally:
        conn.close()


def resolve_pending(user_id: str, channel: str, answer: str) -> IntentResult:
    """Resolve clarification pending menjadi IntentResult merged."""
    pending = load_pending(user_id, channel)
    if not pending:
        raise LookupError("Tidak ada clarification pending untuk user/channel ini")

    original = _intent_from_json(json.loads(pending["original_intent_json"]))
    merged_text = (
        f"Original: {original.raw_message}. "
        f"Klarifikasi: {pending['question']}. "
        f"Jawaban: {answer.strip()}"
    )

    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE clarifications
               SET status='answered', answered_at=CURRENT_TIMESTAMP, answer=?
               WHERE id=?""",
            (answer.strip(), pending["id"]),
        )
        conn.execute(
            "DELETE FROM pending_clarifications WHERE user_id=? AND channel=?",
            (user_id, channel),
        )
        conn.commit()
    finally:
        conn.close()

    return IntentResult(
        mode=original.mode,
        confidence=original.confidence,
        raw_message=merged_text,
        agent_name=original.agent_name,
        method=original.method,
        extras=dict(original.extras),
    )


def should_clarify(
    intent: IntentResult,
    plan: PlanV1 | None,
    decision_card: Any | None,
) -> tuple[bool, str]:
    """Return (True, question) kalau perlu klarifikasi."""
    if plan and plan.clarification_needed:
        question = plan.clarification_questions[0] if plan.clarification_questions else ""
        if question:
            return True, question
        return True, _fallback_question("unscoped_task", intent)

    uncertainty_signals = list(getattr(decision_card, "uncertainty_signals", []) or [])
    if intent.mode == Mode.CONVERSATION and intent.confidence < 0.6:
        uncertainty_signals.append("ambiguous_objective")

    for signal in ("ambiguous_objective", "multiple_candidates", "unscoped_task"):
        if signal in uncertainty_signals:
            return True, _generate_question(intent.raw_message, signal)

    return False, ""


def sweep_timeouts() -> int:
    """Timeout clarification pending yang lewat 30 menit."""
    conn = _get_conn()
    count = 0
    try:
        rows = conn.execute(
            """SELECT id, user_id, channel
               FROM clarifications
               WHERE status='pending'
                 AND asked_at < datetime('now', '-30 minutes')"""
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE clarifications SET status='timeout' WHERE id=?",
                (row["id"],),
            )
            conn.execute(
                "DELETE FROM pending_clarifications WHERE user_id=? AND channel=?",
                (row["user_id"], row["channel"]),
            )
            count += 1
        if count:
            conn.commit()
    except sqlite3.OperationalError as exc:
        logger.warning(f"clarifier: sweep_timeouts skipped: {exc}")
    finally:
        conn.close()
    return count
