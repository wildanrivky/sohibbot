"""ConversationManager — persistent conversation memory per user/channel.

Replaces in-memory _conversation_history dict di telegram_bot.py.
Tabel: conversation_turns, conversation_summaries, conversation_sessions (dari migration 019).
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_SUMMARY_THRESHOLD = 10  # generate summary tiap N turn sejak summary terakhir


@dataclass
class ContextBundle:
    summary: str | None
    recent_turns: list[dict]          # [{user_text, bot_text, created_at}]
    claude_cli_conv_id: str | None    # untuk --resume di call berikutnya


class ConversationManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────────────────────

    def get_context(self, user_id: str, channel: str, max_recent: int = 5) -> ContextBundle:
        """Ambil context: summary terbaru + N turn terakhir + claude_cli_conv_id."""
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT user_text, bot_text, created_at FROM conversation_turns
                   WHERE user_id=? AND channel=?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, channel, max_recent),
            ).fetchall()
            recent_turns = [
                {
                    "user_text": r["user_text"],
                    "bot_text": r["bot_text"],
                    "created_at": r["created_at"],
                }
                for r in reversed(rows)
            ]

            summary_row = conn.execute(
                """SELECT summary FROM conversation_summaries
                   WHERE user_id=? AND channel=?
                   ORDER BY window_end DESC LIMIT 1""",
                (user_id, channel),
            ).fetchone()

            session_row = conn.execute(
                "SELECT claude_cli_conversation_id FROM conversation_sessions WHERE user_id=? AND channel=?",
                (user_id, channel),
            ).fetchone()

            return ContextBundle(
                summary=summary_row["summary"] if summary_row else None,
                recent_turns=recent_turns,
                claude_cli_conv_id=(
                    session_row["claude_cli_conversation_id"] if session_row else None
                ),
            )
        except Exception as exc:
            logger.warning(f"conversation.get_context failed: {exc}")
            return ContextBundle(summary=None, recent_turns=[], claude_cli_conv_id=None)
        finally:
            conn.close()

    def persist_turn(
        self,
        user_id: str,
        channel: str,
        user_text: str,
        bot_text: str | None,
        intent_mode: str | None = None,
        brain_decision: dict | None = None,
        run_id: str | None = None,
        claude_cli_conv_id: str | None = None,
    ) -> int:
        """Simpan satu turn. Return turn_idx baru. Thread-safe."""
        conn = get_connection()
        try:
            with self._lock:
                row = conn.execute(
                    "SELECT MAX(turn_idx) as m FROM conversation_turns WHERE user_id=? AND channel=?",
                    (user_id, channel),
                ).fetchone()
                turn_idx = (row["m"] or 0) + 1
                turn_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO conversation_turns
                       (id, channel, user_id, turn_idx, user_text, bot_text,
                        intent_mode, brain_decision, run_id)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        turn_id, channel, user_id, turn_idx,
                        user_text, bot_text,
                        intent_mode,
                        json.dumps(brain_decision) if brain_decision else None,
                        run_id,
                    ),
                )
                if claude_cli_conv_id:
                    conn.execute(
                        """INSERT OR REPLACE INTO conversation_sessions
                           (user_id, channel, claude_cli_conversation_id, last_turn_idx, updated_at)
                           VALUES (?,?,?,?,CURRENT_TIMESTAMP)""",
                        (user_id, channel, claude_cli_conv_id, turn_idx),
                    )
                conn.commit()
                return turn_idx
        except Exception as exc:
            logger.warning(f"conversation.persist_turn failed: {exc}")
            return 0
        finally:
            conn.close()

    def summarize_if_threshold(
        self, user_id: str, channel: str, threshold: int = _SUMMARY_THRESHOLD
    ) -> None:
        """Background: buat summary baru kalau turn sejak summary terakhir >= threshold."""
        t = threading.Thread(
            target=self._do_summarize,
            args=(user_id, channel, threshold),
            daemon=True,
        )
        t.start()

    def search(self, user_id: str, query: str) -> list[dict]:
        """FTS5 semantic-ish search di conversation history user ini."""
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT ct.user_text, ct.bot_text, ct.created_at
                   FROM conversation_turns_fts fts
                   JOIN conversation_turns ct ON fts.rowid = ct.rowid
                   WHERE conversation_turns_fts MATCH ? AND ct.user_id=?
                   ORDER BY rank LIMIT 10""",
                (query, user_id),
            ).fetchall()
            return [
                {"user_text": r["user_text"], "bot_text": r["bot_text"], "created_at": r["created_at"]}
                for r in rows
            ]
        except Exception as exc:
            logger.warning(f"conversation.search failed: {exc}")
            return []
        finally:
            conn.close()

    # ── Internal ────────────────────────────────────────────────────────────────

    def _do_summarize(self, user_id: str, channel: str, threshold: int) -> None:
        """Panggil Claude CLI ringan untuk buat summary 200-400 char."""
        try:
            conn = get_connection()
            try:
                last_row = conn.execute(
                    """SELECT window_end FROM conversation_summaries
                       WHERE user_id=? AND channel=?
                       ORDER BY window_end DESC LIMIT 1""",
                    (user_id, channel),
                ).fetchone()
                since = f" AND created_at > '{last_row['window_end']}'" if last_row else ""
                count_row = conn.execute(
                    f"SELECT COUNT(*) as cnt FROM conversation_turns WHERE user_id=? AND channel=?{since}",
                    (user_id, channel),
                ).fetchone()
                if (count_row["cnt"] or 0) < threshold:
                    return
                turns = conn.execute(
                    f"""SELECT user_text, bot_text, created_at FROM conversation_turns
                        WHERE user_id=? AND channel=?{since} ORDER BY created_at""",
                    (user_id, channel),
                ).fetchall()
            finally:
                conn.close()

            if not turns:
                return

            lines: list[str] = []
            for t in turns:
                lines.append(f"Wildan: {t['user_text'][:200]}")
                if t["bot_text"]:
                    lines.append(f"El Solver: {t['bot_text'][:200]}")
            transcript = "\n".join(lines)

            from el_solver.llm import call_claude_cli
            prompt = (
                "Ringkas percakapan berikut dalam 200-400 karakter. "
                "Sertakan fakta penting, keputusan, dan konteks yang relevan. "
                'Output JSON: {"summary": "...", "key_points": ["...", "..."]}.\n\n'
                f"Percakapan:\n{transcript[:3000]}"
            )
            result_text, *_ = call_claude_cli(prompt, timeout=60)
            json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
            if not json_match:
                return
            data = json.loads(json_match.group())
            summary = str(data.get("summary", ""))[:500]
            key_points = data.get("key_points", [])

            window_start = turns[0]["created_at"]
            window_end = turns[-1]["created_at"]
            conn2 = get_connection()
            try:
                conn2.execute(
                    """INSERT OR REPLACE INTO conversation_summaries
                       (user_id, channel, window_start, window_end, summary, key_points, turn_count)
                       VALUES (?,?,?,?,?,?,?)""",
                    (user_id, channel, window_start, window_end, summary,
                     json.dumps(key_points), len(turns)),
                )
                conn2.commit()
                logger.info(f"conversation: summary generated {user_id}/{channel} ({len(turns)} turns)")
            finally:
                conn2.close()

        except Exception as exc:
            logger.warning(f"conversation._do_summarize failed: {exc}")


# ── Singleton ────────────────────────────────────────────────────────────────

_manager: ConversationManager | None = None


def get_manager() -> ConversationManager:
    global _manager
    if _manager is None:
        _manager = ConversationManager()
    return _manager
