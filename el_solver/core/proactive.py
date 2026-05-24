"""
Proactive Engine — R12.

Jadwalkan, filter via relevance gate, dan kirimkan pesan proaktif ke user.
Tidak ada unconditional cron — setiap pesan harus lulus relevance gate.

Relevance gate (tanpa LLM — heuristic):
  1. Jam aktif: 07:00–22:00 WIB
  2. Tidak duplikat (belum ada pesan sama tipe dalam 2 jam terakhir)
  3. Max 3 proactives per user per hari
  4. Tipe post_task_followup: hanya dikirim kalau task masih open
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_WIB = timezone(timedelta(hours=7))
_ACTIVE_HOURS = (7, 22)   # WIB
_MAX_PER_DAY = 3
_DEDUP_WINDOW_H = 2


def _now_wib() -> datetime:
    return datetime.now(_WIB)


def schedule_followup(
    user_id: str,
    channel: str,
    trigger_type: str,
    message: str,
    scheduled_at: datetime | None = None,
    signal_payload: dict | None = None,
) -> str:
    """Jadwalkan proactive followup. Return followup_id."""
    if scheduled_at is None:
        scheduled_at = _now_wib() + timedelta(minutes=15)

    fid = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO proactive_followups
               (id, user_id, channel, trigger_type, signal_payload,
                scheduled_at, status, message_preview)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                fid, user_id, channel, trigger_type,
                json.dumps(signal_payload or {}),
                scheduled_at.isoformat(),
                "pending",
                message[:500],
            ),
        )
        conn.commit()
        logger.info(f"proactive: scheduled {trigger_type} for {user_id}@{channel} at {scheduled_at.isoformat()[:16]}")
        return fid
    except Exception as e:
        logger.warning(f"proactive: schedule_followup failed: {e}")
        return fid
    finally:
        conn.close()


def _passes_relevance_gate(followup: dict) -> tuple[bool, str]:
    """
    Return (pass, reason_if_failed).
    Heuristic gate — no LLM required.
    """
    conn = get_connection()
    user_id = followup["user_id"]
    channel = followup["channel"]
    trigger_type = followup["trigger_type"]

    try:
        now_wib = _now_wib()

        # Gate 1: Jam aktif
        if not (_ACTIVE_HOURS[0] <= now_wib.hour < _ACTIVE_HOURS[1]):
            return False, f"di luar jam aktif ({now_wib.hour}:xx WIB)"

        # Gate 2: Dedup — ada pesan tipe sama dalam N jam terakhir?
        cutoff = (now_wib - timedelta(hours=_DEDUP_WINDOW_H)).isoformat()
        dup = conn.execute(
            """SELECT COUNT(*) as n FROM proactive_followups
               WHERE user_id=? AND channel=? AND trigger_type=?
                 AND status='sent' AND sent_at > ?""",
            (user_id, channel, trigger_type, cutoff),
        ).fetchone()
        if dup and dup["n"] > 0:
            return False, f"duplikat {trigger_type} dalam {_DEDUP_WINDOW_H}j terakhir"

        # Gate 3: Max per hari
        today_start = now_wib.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        daily = conn.execute(
            """SELECT COUNT(*) as n FROM proactive_followups
               WHERE user_id=? AND channel=? AND status='sent' AND sent_at > ?""",
            (user_id, channel, today_start),
        ).fetchone()
        if daily and daily["n"] >= _MAX_PER_DAY:
            return False, f"sudah {_MAX_PER_DAY} proactive hari ini"

        return True, ""

    except Exception as e:
        logger.warning(f"relevance_gate error: {e}")
        return True, ""  # fail-open: kalau gate error, biarkan lewat
    finally:
        conn.close()


def sweep_pending() -> list[dict]:
    """
    Fetch pending followups yang sudah scheduled, jalankan relevance gate.
    Return list followup yang lulus gate (siap dikirim), sudah di-mark 'sending'.
    Followup yang gagal gate di-mark 'suppressed'.
    """
    conn = get_connection()
    now = _now_wib().isoformat()
    try:
        rows = conn.execute(
            """SELECT id, user_id, channel, trigger_type, message_preview,
                      signal_payload, scheduled_at
               FROM proactive_followups
               WHERE status='pending' AND scheduled_at <= ?
               ORDER BY scheduled_at ASC LIMIT 20""",
            (now,),
        ).fetchall()
    except Exception as e:
        logger.warning(f"proactive sweep query failed: {e}")
        return []
    finally:
        conn.close()

    ready = []
    for row in rows:
        followup = dict(row)
        passed, reason = _passes_relevance_gate(followup)
        if passed:
            _mark_status(followup["id"], "sending")
            ready.append(followup)
        else:
            _mark_suppressed(followup["id"], reason)
            logger.info(f"proactive: suppressed {followup['trigger_type']} — {reason}")

    return ready


def mark_sent(followup_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE proactive_followups SET status='sent', sent_at=? WHERE id=?",
            (_now_wib().isoformat(), followup_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"proactive mark_sent failed: {e}")
    finally:
        conn.close()


def mark_failed(followup_id: str, reason: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE proactive_followups SET status='failed', suppressed_reason=? WHERE id=?",
            (reason, followup_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _mark_status(followup_id: str, status: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE proactive_followups SET status=? WHERE id=?",
            (status, followup_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _mark_suppressed(followup_id: str, reason: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE proactive_followups SET status='suppressed', suppressed_reason=? WHERE id=?",
            (reason, followup_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def get_outbox(limit: int = 50) -> list[dict]:
    """Fetch proactive outbox untuk /inbox tab."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, user_id, channel, trigger_type, message_preview,
                      scheduled_at, status, suppressed_reason, sent_at, created_at
               FROM proactive_followups
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_outbox failed: {e}")
        return []
    finally:
        conn.close()


# ── Morning Briefing builder ─────────────────────────────────────────────────

def create_morning_briefing(user_id: str, channel: str) -> str | None:
    """
    Buat morning briefing berdasarkan tasks pending + agent status.
    Return followup_id yang baru di-schedule, atau None kalau tidak ada yang relevan.
    """
    conn = get_connection()
    try:
        # Cek ada agent yang paused?
        paused = conn.execute(
            "SELECT COUNT(*) as n FROM agents_registry WHERE status='paused'"
        ).fetchone()
        n_paused = paused["n"] if paused else 0

        # Cek ada approvals pending?
        pending_approvals = conn.execute(
            "SELECT COUNT(*) as n FROM approvals WHERE status='pending'"
        ).fetchone()
        n_approvals = pending_approvals["n"] if pending_approvals else 0

        # Cek ada task yang stuck (running > 30 menit)?
        stuck = conn.execute(
            """SELECT COUNT(*) as n FROM runs
               WHERE status='running'
                 AND started_at < datetime('now', '-30 minutes')"""
        ).fetchone()
        n_stuck = stuck["n"] if stuck else 0

    except Exception as e:
        logger.warning(f"morning briefing query failed: {e}")
        return None
    finally:
        conn.close()

    lines = []
    if n_paused:
        lines.append(f"- {n_paused} agent paused (circuit breaker open)")
    if n_approvals:
        lines.append(f"- {n_approvals} approval menunggu keputusan")
    if n_stuck:
        lines.append(f"- {n_stuck} task stuck (running > 30 menit)")

    if not lines:
        return None  # Tidak ada yang perlu dilaporkan → relevance gate akan suppressed anyway

    now_wib = _now_wib()
    scheduled_at = now_wib.replace(hour=7, minute=0, second=0, microsecond=0)
    if now_wib >= scheduled_at:
        scheduled_at = scheduled_at + timedelta(days=1)

    message = "Selamat pagi! Status EL SOLVER:\n" + "\n".join(lines)
    return schedule_followup(
        user_id=user_id,
        channel=channel,
        trigger_type="morning_briefing",
        message=message,
        scheduled_at=scheduled_at,
        signal_payload={"n_paused": n_paused, "n_approvals": n_approvals, "n_stuck": n_stuck},
    )
