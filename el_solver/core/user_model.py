"""User Model Auto-update — extract preferensi Wildan dari conversation history.

Jalan via scheduler (daily 01:00 WIB) atau triggered manual.
Scan conversation_turns, extract pola preferensi baru, append ke memory/user/preferences.md.
Tidak pernah overwrite isi lama — hanya append delta section.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from el_solver.config import PROJECT_ROOT
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_MEMORY_USER_DIR = PROJECT_ROOT / "memory" / "user"
_PREFERENCES_FILE = _MEMORY_USER_DIR / "preferences.md"
_CHECKPOINT_FILE = _MEMORY_USER_DIR / "model_checkpoint.json"
_TRIGGER_EVERY_N_TURNS = 20   # update kalau ada ≥20 turn baru sejak checkpoint
_MIN_TURNS_FOR_ANALYSIS = 10  # butuh minimal 10 turn untuk analisis meaningful


def _load_checkpoint() -> dict:
    if _CHECKPOINT_FILE.exists():
        try:
            return json.loads(_CHECKPOINT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_turn_count": 0, "last_updated": None}


def _save_checkpoint(turn_count: int) -> None:
    _MEMORY_USER_DIR.mkdir(parents=True, exist_ok=True)
    _CHECKPOINT_FILE.write_text(
        json.dumps({"last_turn_count": turn_count, "last_updated": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_total_turns() -> int:
    try:
        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) as cnt FROM conversation_turns").fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


def _get_recent_turns(limit: int = 30) -> list[dict]:
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT user_text, bot_text, created_at FROM conversation_turns
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"user": r["user_text"] or "", "bot": r["bot_text"] or "", "at": r["created_at"] or ""}
            for r in reversed(rows)
        ]
    except Exception:
        return []


def _build_analysis_prompt(turns: list[dict]) -> str:
    turns_text = "\n".join(
        f"[{t['at'][:16]}] Wildan: {t['user'][:150]}\n"
        f"         Bot: {t['bot'][:150]}"
        for t in turns
    )
    return (
        "Kamu adalah analyst yang membaca percakapan antara Wildan dan EL SOLVER. "
        "Tujuan: identifikasi pola preferensi BARU yang belum ada di catatan.\n\n"
        f"=== 30 PERCAKAPAN TERAKHIR ===\n{turns_text}\n\n"
        "=== TUGAS ===\n"
        "Identifikasi HANYA preferensi yang jelas terlihat dari percakapan ini:\n"
        "1. Gaya komunikasi (panjang respon, format, bahasa)\n"
        "2. Pola request yang sering (apa yang sering diminta)\n"
        "3. Hal yang Wildan tidak suka atau koreksi\n"
        "4. Konteks situasional (kapan aktif, topik dominan)\n\n"
        "Format output PERSIS:\n"
        "## Observasi Baru [YYYY-MM-DD]\n"
        "- [preferensi/pola 1]\n"
        "- [preferensi/pola 2]\n"
        "(dst, maksimal 5 poin)\n\n"
        "Kalau tidak ada preferensi baru yang jelas terdeteksi, tulis:\n"
        "## Observasi Baru [YYYY-MM-DD]\n"
        "_(tidak ada pola baru terdeteksi)_\n\n"
        "Bahasa Indonesia. Singkat. TIDAK ada intro atau penjelasan tambahan."
    )


def _append_to_preferences(new_section: str) -> None:
    _MEMORY_USER_DIR.mkdir(parents=True, exist_ok=True)
    if not new_section.strip():
        return
    existing = _PREFERENCES_FILE.read_text(encoding="utf-8") if _PREFERENCES_FILE.exists() else ""
    updated = existing.rstrip() + "\n\n" + new_section.strip() + "\n"
    _PREFERENCES_FILE.write_text(updated, encoding="utf-8")


def update_user_model(force: bool = False) -> bool:
    """Scan conversation history dan update preferences.md kalau ada pola baru.

    Return True kalau update dilakukan, False kalau skip (tidak cukup data atau belum waktunya).
    """
    total_turns = _get_total_turns()
    checkpoint = _load_checkpoint()
    last_count = checkpoint.get("last_turn_count", 0)
    new_turns = total_turns - last_count

    if not force and new_turns < _TRIGGER_EVERY_N_TURNS:
        logger.info(
            f"user_model: skip — hanya {new_turns} turn baru (threshold {_TRIGGER_EVERY_N_TURNS})"
        )
        return False

    if total_turns < _MIN_TURNS_FOR_ANALYSIS:
        logger.info(f"user_model: skip — total {total_turns} turns kurang dari minimum {_MIN_TURNS_FOR_ANALYSIS}")
        return False

    logger.info(f"user_model: mulai analisis ({new_turns} turn baru, total {total_turns})")

    recent_turns = _get_recent_turns(limit=30)
    if not recent_turns:
        logger.warning("user_model: tidak ada turns untuk dianalisis")
        return False

    prompt = _build_analysis_prompt(recent_turns)
    try:
        from el_solver.llm import call_claude_cli
        result, *_ = call_claude_cli(prompt, timeout=120)
    except Exception as exc:
        logger.error(f"user_model: Claude CLI gagal: {exc}")
        return False

    if not result or not result.strip():
        logger.warning("user_model: output Claude kosong")
        return False

    _append_to_preferences(result.strip())
    _save_checkpoint(total_turns)
    logger.info("user_model: preferences.md diupdate")
    return True


def register_user_model_cron(scheduler) -> None:
    """Register daily job: setiap hari 01:00 WIB."""
    try:
        from apscheduler.triggers.cron import CronTrigger
        job_id = "user_model_daily"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        scheduler.add_job(
            update_user_model,
            trigger=CronTrigger(hour=1, minute=0, timezone="Asia/Jakarta"),
            id=job_id,
            replace_existing=True,
        )
        logger.info("user_model: registered cron daily 01:00 WIB")
    except Exception as exc:
        logger.error(f"user_model: gagal register cron: {exc}")
