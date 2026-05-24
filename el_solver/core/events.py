"""Event Bus — emit_event() helper untuk milestone tracking."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


def emit_event(
    type: str,
    payload: dict[str, Any] | None = None,
    agent: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    parent: str | None = None,
    duration_ms: int | None = None,
    cost: float | None = None,
) -> str:
    """Insert event ke tabel events. Return event_id. Never raises — errors are logged."""
    event_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO events
               (event_id, timestamp, type, agent, task_id, run_id, parent_event_id, payload, duration_ms, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                datetime.now(timezone.utc).isoformat(),
                type,
                agent,
                task_id,
                run_id,
                parent,
                json.dumps(payload or {}),
                duration_ms,
                cost,
            ),
        )
        conn.commit()
        logger.debug(f"emit_event: {type} agent={agent} task_id={task_id}")
    except Exception as exc:
        logger.warning(f"emit_event failed (non-critical): {exc}")
    finally:
        conn.close()
    return event_id


def get_events_for_task(task_id: str) -> list[dict]:
    """Fetch all events for a task_id ordered by timestamp."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT event_id, timestamp, type, agent, task_id, run_id,
                      parent_event_id, payload, duration_ms, cost_usd
               FROM events WHERE task_id=? ORDER BY timestamp ASC""",
            (task_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except Exception:
                d["payload"] = {}
            result.append(d)
        return result
    except Exception:
        return []
    finally:
        conn.close()


def get_events_for_run(run_id: str) -> list[dict]:
    """Fetch all events for a run_id ordered by timestamp."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT event_id, timestamp, type, agent, task_id, run_id,
                      parent_event_id, payload, duration_ms, cost_usd
               FROM events WHERE run_id=? ORDER BY timestamp ASC""",
            (run_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except Exception:
                d["payload"] = {}
            result.append(d)
        return result
    except Exception:
        return []
    finally:
        conn.close()
