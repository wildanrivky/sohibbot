"""
Confidence Scorer — Round 1 implementation.

Hanya history signal (40%). Rest default 0.7 (R2+ akan tambah schema/consistency/self_report).
Multi-signal penuh di V2.

Reference (blueprint section 7.4):
  history      40% — pre-execution, dari DB conversations/runs
  schema       30% — post-execution (default 0.7 di R1)
  consistency  20% — skip di subscription mode (rate limit + mahal), default 0.8
  self_report  10% — skip di subscription mode, default 0.8
"""
from __future__ import annotations

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_WEIGHTS = {
    "history": 0.4,
    "schema": 0.3,
    "consistency": 0.2,
    "self_report": 0.1,
}

_DEFAULTS = {
    "schema": 0.7,
    "consistency": 0.8,
    "self_report": 0.8,
}


def compute_confidence(agent: str, task_signature: str = "") -> tuple[float, dict]:
    """
    Hitung confidence score sebelum eksekusi.

    Return: (confidence: float 0.0-1.0, signals: dict)
    """
    signals: dict[str, float] = {}

    # 1. History signal (40%) — sukses rate 30 hari terakhir
    signals["history"] = _history_signal(agent, task_signature)

    # 2-4. Default values (R1 — multi-signal di R2+)
    signals["schema"] = _DEFAULTS["schema"]
    signals["consistency"] = _DEFAULTS["consistency"]
    signals["self_report"] = _DEFAULTS["self_report"]

    confidence = sum(signals[k] * _WEIGHTS[k] for k in _WEIGHTS)

    logger.debug(f"confidence: agent={agent} signals={signals} score={confidence:.3f}")
    return confidence, signals


def _history_signal(agent: str, task_signature: str) -> float:
    """
    Sukses rate agent dalam 30 hari terakhir dari tabel runs.
    Return 0.5 (neutral) kalau tidak ada data.
    """
    conn = get_connection()
    try:
        # Query runs untuk agent ini, 30 hari terakhir
        row = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successes
               FROM runs
               WHERE agent_name=?
               AND started_at > datetime('now', '-30 days')""",
            (agent,),
        ).fetchone()

        if not row or not row["total"] or row["total"] == 0:
            return 0.5  # Tidak ada data → neutral

        success_rate = row["successes"] / row["total"]
        return round(success_rate, 4)
    except Exception as e:
        logger.debug(f"history_signal query failed: {e}")
        return 0.5
    finally:
        conn.close()
