"""Scheduler — APScheduler BackgroundScheduler untuk cron agent otomatis."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "el-solver.db"

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False
    logger.warning("apscheduler tidak terinstall — scheduler dinonaktifkan")

_scheduler: "BackgroundScheduler | None" = None
_last_registered: dict[str, str] = {}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


_CIRCUIT_FAIL_WINDOW_MIN = 30   # menit window cek kegagalan
_CIRCUIT_FAIL_THRESHOLD  = 5    # jumlah gagal sebelum trip
_CIRCUIT_COOLOFF_MIN     = 60   # menit cooloff sebelum auto-resume


def _check_circuit(conn: sqlite3.Connection, agent_name: str) -> bool:
    """Return True kalau agent boleh jalan. False kalau paused (circuit open)."""
    from datetime import timedelta
    row = conn.execute(
        "SELECT status, paused_at, failure_count, last_failure_at FROM agents_registry WHERE name=?",
        (agent_name,),
    ).fetchone()
    if not row:
        return True

    status = row["status"] or "active"
    paused_at_str = row["paused_at"]

    if status == "paused" and paused_at_str:
        paused_at = datetime.fromisoformat(paused_at_str.replace("Z", "+00:00"))
        if paused_at.tzinfo is None:
            paused_at = paused_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now >= paused_at + timedelta(minutes=_CIRCUIT_COOLOFF_MIN):
            # Auto-resume setelah cooloff
            conn.execute(
                "UPDATE agents_registry SET status='active', failure_count=0, paused_at=NULL WHERE name=?",
                (agent_name,),
            )
            conn.commit()
            logger.info(f"circuit: auto-resume '{agent_name}' setelah {_CIRCUIT_COOLOFF_MIN}m cooloff")
            try:
                from el_solver.core.events import emit_event
                emit_event("circuit.closed", {"agent": agent_name, "reason": "cooloff_expired"},
                           agent=agent_name)
            except Exception:
                pass
            return True
        logger.warning(f"scheduler: '{agent_name}' paused (circuit open), skip")
        return False

    return True


def _record_failure(conn: sqlite3.Connection, agent_name: str) -> None:
    """Catat kegagalan dengan sliding window 30 menit. Trip circuit breaker kalau ≥5 dalam window."""
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # Baca state saat ini SEBELUM update
    row = conn.execute(
        "SELECT failure_count, last_failure_at FROM agents_registry WHERE name=?",
        (agent_name,),
    ).fetchone()

    current_count = 0
    if row:
        last_fail_str = row["last_failure_at"]
        if last_fail_str:
            last_fail = datetime.fromisoformat(last_fail_str.replace("Z", "+00:00"))
            if last_fail.tzinfo is None:
                last_fail = last_fail.replace(tzinfo=timezone.utc)
            if (now - last_fail).total_seconds() > _CIRCUIT_FAIL_WINDOW_MIN * 60:
                # Window expired → mulai window baru dengan count=1
                current_count = 0
            else:
                current_count = row["failure_count"] or 0
        # last_failure_at NULL → anggap window baru

    new_count = current_count + 1
    conn.execute(
        """UPDATE agents_registry
           SET failure_count = ?,
               last_failure_at = ?
           WHERE name=?""",
        (new_count, now_iso, agent_name),
    )
    conn.commit()

    if new_count >= _CIRCUIT_FAIL_THRESHOLD:
        # Trip circuit breaker
        conn.execute(
            "UPDATE agents_registry SET status='paused', paused_at=? WHERE name=?",
            (now_iso, agent_name),
        )
        _insert_budget_alert(conn, agent_name, "circuit_open",
                             f"Agent {agent_name} di-pause (circuit breaker): "
                             f"{new_count} kegagalan dalam {_CIRCUIT_FAIL_WINDOW_MIN} menit.")
        conn.commit()
        logger.error(f"circuit: OPENED untuk '{agent_name}' setelah {new_count} kegagalan dalam window")
        try:
            from el_solver.core.events import emit_event
            emit_event("circuit.opened", {"agent": agent_name, "failure_count": new_count},
                       agent=agent_name)
        except Exception:
            pass


def _record_success(conn: sqlite3.Connection, agent_name: str) -> None:
    """Reset failure_count setelah sukses."""
    conn.execute(
        "UPDATE agents_registry SET failure_count=0, last_failure_at=NULL WHERE name=?",
        (agent_name,),
    )
    conn.commit()


def _insert_dlq(agent_name: str, run_id: str | None, payload: dict, error: str) -> None:
    """Insert ke dead_letter_queue."""
    import json as _json
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO dead_letter_queue (id, agent, run_id, payload, error) VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), agent_name, run_id, _json.dumps(payload), error[:500]),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"_insert_dlq failed: {e}")
    finally:
        conn.close()


def _run_agent_job(agent_name: str, default_input: str = "jalankan task rutin") -> None:
    """Job body yang dipanggil APScheduler."""
    logger.info(f"scheduler: memulai job untuk agent '{agent_name}'")
    conn = _get_conn()
    try:
        # Circuit breaker check — skip jika paused
        if not _check_circuit(conn, agent_name):
            conn.close()
            return

        row = conn.execute(
            "SELECT token_spent_today, token_limit_daily FROM agents_registry WHERE name=?",
            (agent_name,),
        ).fetchone()

        if row and (row["token_spent_today"] or 0) >= (row["token_limit_daily"] or 50000):
            logger.warning(f"scheduler: {agent_name} budget habis, skip")
            _insert_budget_alert(conn, agent_name, "hard_stop",
                                 f"Agent {agent_name} dilewati scheduler karena budget harian habis.")
            conn.commit()
            conn.close()
            return

        conn.close()

        from el_solver.channels import handler as msg_handler
        from el_solver.core.orchestrator import IntentResult, Mode
        import asyncio

        intent = IntentResult(
            mode=Mode.INVOKE_AGENT,
            confidence=1.0,
            raw_message=default_input,
            agent_name=agent_name,
        )
        asyncio.run(msg_handler.handle(intent, channel="scheduler", user_id="scheduler"))

        # Sukses → reset failure count
        conn2 = _get_conn()
        _record_success(conn2, agent_name)
        conn2.close()
        logger.info(f"scheduler: job '{agent_name}' selesai")

    except Exception as exc:
        logger.error(f"scheduler: job '{agent_name}' gagal: {exc}")
        conn3 = _get_conn()
        _record_failure(conn3, agent_name)
        _insert_dlq(agent_name, None, {"input": default_input, "trigger": "scheduler"}, str(exc))
        conn3.close()
        # Real-time skill gap detection — proposal langsung tanpa tunggu weekly batch
        try:
            from el_solver.core.gap_detector import detect_single_gap
            detect_single_gap(agent_name, str(exc), source_type="scheduler_failure")
        except Exception:
            pass


def _insert_budget_alert(conn: sqlite3.Connection, agent_name: str, alert_type: str, message: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO budget_alerts (id, agent_name, type, message) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), agent_name, alert_type, message),
    )


def clarification_timeout_sweep() -> int:
    """Timeout clarification pending yang lewat 30 menit."""
    try:
        from el_solver.core.clarifier import sweep_timeouts
        return sweep_timeouts()
    except Exception as exc:
        logger.warning(f"scheduler: clarification timeout sweep gagal: {exc}")
        return 0


def skill_gap_weekly() -> int:
    """Weekly skill gap sweep — proposal baru untuk human review."""
    try:
        from el_solver.core.gap_detector import run_gap_detector
        saved = run_gap_detector(window_days=7)
        return len(saved)
    except Exception as exc:
        logger.warning(f"scheduler: skill gap sweep gagal: {exc}")
        return 0


def load_scheduled_agents() -> list[str]:
    """Query agents_registry dan daftarkan cron job untuk tiap agent dengan schedule."""
    if not _HAS_APSCHEDULER or _scheduler is None:
        return []

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT name, schedule FROM agents_registry WHERE schedule IS NOT NULL AND schedule != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    conn.close()

    loaded: list[str] = []
    for row in rows:
        name = row["name"]
        schedule = row["schedule"]
        try:
            parts = schedule.strip().split()
            if len(parts) == 5:
                minute, hour, day, month, day_of_week = parts
                trigger = CronTrigger(
                    minute=minute,
                    hour=hour,
                    day=day,
                    month=month,
                    day_of_week=day_of_week,
                )
                job_id = f"agent_{name}"
                if _scheduler.get_job(job_id):
                    _scheduler.remove_job(job_id)
                _scheduler.add_job(
                    _run_agent_job,
                    trigger=trigger,
                    id=job_id,
                    args=[name],
                    replace_existing=True,
                )
                loaded.append(name)
                if _last_registered.get(name) != schedule:
                    logger.info(f"scheduler: registered '{name}' cron '{schedule}'")
                    _last_registered[name] = schedule
            else:
                logger.warning(f"scheduler: schedule invalid untuk '{name}': {schedule!r}")
        except Exception as exc:
            logger.error(f"scheduler: gagal daftarkan '{name}': {exc}")

    return loaded


# ── R12: Proactive job functions ──────────────────────────────────────────────

def proactive_sweep_job() -> None:
    """
    Cron job tiap 15 menit.
    Sweep pending proactive followups, apply relevance gate, kirim via Telegram Bot API.
    """
    try:
        from el_solver.core.proactive import sweep_pending, mark_sent, mark_failed
        from el_solver.config import settings
        import requests as _requests

        ready = sweep_pending()
        if not ready:
            return

        for fp in ready:
            try:
                # Kirim via Telegram Bot API (direct, tidak butuh bot instance)
                token = settings.active_telegram_bot_token
                owner = settings.telegram_owner_id
                if not token or not owner:
                    mark_failed(fp["id"], "token/owner not configured")
                    continue

                text = fp["message_preview"] or "(proactive)"
                resp = _requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": owner, "text": text},
                    timeout=10,
                )
                if resp.ok:
                    mark_sent(fp["id"])
                    logger.info(f"proactive: sent {fp['trigger_type']} to {fp['user_id']}")
                else:
                    mark_failed(fp["id"], f"Telegram API {resp.status_code}")
            except Exception as exc:
                mark_failed(fp["id"], str(exc))
                logger.warning(f"proactive: send failed for {fp['id']}: {exc}")
    except Exception as e:
        logger.warning(f"proactive_sweep_job error: {e}")


def proactive_morning_briefing_job() -> None:
    """
    Cron job 06:50 WIB.
    Buat morning briefing entry untuk besok pagi 07:00 WIB.
    """
    try:
        from el_solver.core.proactive import create_morning_briefing
        from el_solver.config import settings

        owner = settings.telegram_owner_id
        if not owner:
            return

        fid = create_morning_briefing(str(owner), "telegram")
        if fid:
            logger.info(f"proactive: morning_briefing scheduled: {fid[:8]}")
        else:
            logger.debug("proactive: morning_briefing skipped (nothing to report)")
    except Exception as e:
        logger.warning(f"proactive_morning_briefing_job error: {e}")


def start(foreground: bool = False) -> None:
    """Start scheduler. foreground=True untuk debug (blocking)."""
    global _scheduler

    if not _HAS_APSCHEDULER:
        logger.error("apscheduler tidak terinstall. Jalankan: pip install apscheduler>=3.10")
        return

    _scheduler = BackgroundScheduler(timezone="Asia/Jakarta")
    _scheduler.start()

    agents = load_scheduled_agents()
    logger.info(f"scheduler: started. Loaded {len(agents)} scheduled agent(s): {agents}")

    try:
        from el_solver.core.pattern_miner import register_pattern_miner_cron
        register_pattern_miner_cron(_scheduler)
    except Exception as _pm_exc:
        logger.warning(f"scheduler: pattern_miner cron tidak terdaftar: {_pm_exc}")

    try:
        from el_solver.core.user_model import register_user_model_cron
        register_user_model_cron(_scheduler)
    except Exception as _um_exc:
        logger.warning(f"scheduler: user_model cron tidak terdaftar: {_um_exc}")

    try:
        from apscheduler.triggers.interval import IntervalTrigger
        job_id = "clarification_timeout_sweep"
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)
        _scheduler.add_job(
            clarification_timeout_sweep,
            trigger=IntervalTrigger(minutes=5),
            id=job_id,
            replace_existing=True,
        )
        logger.info("scheduler: registered clarification timeout sweep tiap 5 menit")
    except Exception as exc:
        logger.warning(f"scheduler: clarification timeout sweep tidak terdaftar: {exc}")

    try:
        from apscheduler.triggers.cron import CronTrigger as _CronTrigger
        job_id = "skill_gap_weekly"
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)
        _scheduler.add_job(
            skill_gap_weekly,
            trigger=_CronTrigger(day_of_week="mon", hour=3, minute=0, timezone="Asia/Jakarta"),
            id=job_id,
            replace_existing=True,
        )
        logger.info("scheduler: registered skill gap weekly tiap Senin 03:00 WIB")
    except Exception as exc:
        logger.warning(f"scheduler: skill gap weekly tidak terdaftar: {exc}")

    # ── R12: Proactive Engine jobs ──────────────────────────────────────────
    try:
        from apscheduler.triggers.interval import IntervalTrigger as _ITrigger
        job_id = "proactive_sweep"
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)
        _scheduler.add_job(
            proactive_sweep_job,
            trigger=_ITrigger(minutes=15),
            id=job_id,
            replace_existing=True,
        )
        logger.debug("scheduler: registered proactive_sweep tiap 15 menit")
    except Exception as exc:
        logger.warning(f"scheduler: proactive_sweep tidak terdaftar: {exc}")

    try:
        from apscheduler.triggers.cron import CronTrigger as _CronTrigger
        job_id = "proactive_morning_briefing"
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)
        _scheduler.add_job(
            proactive_morning_briefing_job,
            trigger=_CronTrigger(hour=6, minute=50, timezone="Asia/Jakarta"),
            id=job_id,
            replace_existing=True,
        )
        logger.debug("scheduler: registered proactive_morning_briefing tiap 06:50 WIB")
    except Exception as exc:
        logger.warning(f"scheduler: proactive_morning_briefing tidak terdaftar: {exc}")

    if foreground:
        import time
        print(f"Scheduler berjalan. Loaded {len(agents)} agent: {agents}")
        print("Tekan Ctrl+C untuk berhenti.")
        try:
            while True:
                time.sleep(30)
                _reload_jobs()
        except (KeyboardInterrupt, SystemExit):
            _scheduler.shutdown()
            print("Scheduler dihentikan.")


def _reload_jobs() -> None:
    """Reload cron jobs dari DB (untuk hot-reload schedule changes)."""
    if _scheduler is None:
        return
    current_jobs = {j.id for j in _scheduler.get_jobs()}
    for job_id in list(current_jobs):
        if job_id.startswith("agent_"):
            _scheduler.remove_job(job_id)
    load_scheduled_agents()


def check_budget(agent_name: str) -> tuple[bool, str]:
    """Return (ok, reason). ok=False jika budget habis."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT token_spent_today, token_limit_daily, token_reset_at FROM agents_registry WHERE name=?",
            (agent_name,),
        ).fetchone()
    except sqlite3.OperationalError:
        conn.close()
        return True, ""

    if row is None:
        conn.close()
        return True, ""

    spent = row["token_spent_today"] or 0
    limit = row["token_limit_daily"] or 50000
    pct = (spent / limit * 100) if limit else 0

    if spent >= limit:
        conn.close()
        return False, f"Budget harian habis ({spent}/{limit} token)"

    if pct >= 80:
        _insert_budget_alert(conn, agent_name, "warning_80",
                             f"{agent_name} sudah pakai {spent:,} token ({pct:.1f}%) dari limit {limit:,}.")
        conn.commit()

    conn.close()
    return True, ""


def record_tokens(agent_name: str, tokens_used: int) -> None:
    """Update token_spent_today setelah run selesai."""
    conn = _get_conn()
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        row = conn.execute(
            "SELECT token_reset_at FROM agents_registry WHERE name=?", (agent_name,)
        ).fetchone()

        reset_at = row["token_reset_at"] if row else None
        if reset_at and reset_at[:10] < today:
            conn.execute(
                "UPDATE agents_registry SET token_spent_today=0, token_reset_at=? WHERE name=?",
                (today, agent_name),
            )

        conn.execute(
            "UPDATE agents_registry SET token_spent_today = COALESCE(token_spent_today,0) + ?, token_reset_at=COALESCE(token_reset_at,?) WHERE name=?",
            (tokens_used, today, agent_name),
        )

        row2 = conn.execute(
            "SELECT token_spent_today, token_limit_daily FROM agents_registry WHERE name=?",
            (agent_name,),
        ).fetchone()
        if row2:
            spent = row2["token_spent_today"] or 0
            limit = row2["token_limit_daily"] or 50000
            if spent >= limit:
                _insert_budget_alert(conn, agent_name, "hard_stop",
                                     f"{agent_name} mencapai hard stop: {spent:,}/{limit:,} token.")

        conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
