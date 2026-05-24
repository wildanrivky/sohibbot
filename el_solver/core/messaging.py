"""Inter-agent messaging bus (R13 M4).

The blueprint (Section 6.4) defines a fixed message vocabulary for the
agent mesh:

    GM      ──Goal──────────────►  Head
    Head    ──WorkerTask────────►  Worker
    Worker  ──Result────────────►  Head
    Head    ──ExecutiveSummary─►  GM

Every message is persisted to the ``mesh_messages`` table so the R14+
eval loop can reconstruct who asked whom to do what, and how it went.

This module only models + records messages. Routing/dispatch stays in
``orchestrator_chain`` (extended in M5).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS mesh_messages (
  id TEXT PRIMARY KEY,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  kind TEXT NOT NULL,
  from_agent TEXT,
  to_agent TEXT,
  goal_id TEXT,
  task_id TEXT,
  status TEXT,
  payload TEXT
);
"""


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Message types ─────────────────────────────────────────────────────────────

@dataclass
class Goal:
    """GM → Head. A long-horizon objective with success criteria + budget."""

    description: str
    success_criteria: str = ""
    budget_tokens: int = 0
    id: str = field(default_factory=_new_id)
    metadata: dict[str, Any] = field(default_factory=dict)

    kind = "goal"


@dataclass
class WorkerTask:
    """Head → Worker. A single concrete unit of work under a Goal."""

    description: str
    agent: str
    goal_id: str = ""
    context: str = ""
    id: str = field(default_factory=_new_id)

    kind = "worker_task"


@dataclass
class Result:
    """Worker → Head. Output + self-assessment + cost."""

    task_id: str
    agent: str
    status: str  # completed | error
    output: str = ""
    summary: str = ""
    confidence: float = 1.0
    cost_tokens: int = 0
    error: str | None = None

    kind = "result"

    @property
    def ok(self) -> bool:
        return self.status == "completed" and self.error is None


@dataclass
class ExecutiveSummary:
    """Head → GM. Rolled-up verdict for a Goal."""

    goal_id: str
    status: str  # completed | partial | failed
    summary: str
    results: list[dict[str, Any]] = field(default_factory=list)
    id: str = field(default_factory=_new_id)

    kind = "executive_summary"


# ── Bus ───────────────────────────────────────────────────────────────────────

class MessageBus:
    """Records mesh messages to ``mesh_messages`` and reads them back."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = get_connection(self.db_path)
        try:
            conn.executescript(_ENSURE_SQL)
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        message: Goal | WorkerTask | Result | ExecutiveSummary,
        *,
        from_agent: str | None = None,
        to_agent: str | None = None,
    ) -> str:
        """Persist a message. Returns the row id."""
        kind = message.kind
        goal_id = getattr(message, "goal_id", "") or getattr(message, "id", "")
        task_id = getattr(message, "task_id", "") or ""
        status = getattr(message, "status", "") or ""
        row_id = getattr(message, "id", None) or _new_id()
        payload = json.dumps(asdict(message), ensure_ascii=False)

        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO mesh_messages
                   (id, kind, from_agent, to_agent, goal_id, task_id, status, payload)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (row_id, kind, from_agent, to_agent, goal_id, task_id, status, payload),
            )
            conn.commit()
        finally:
            conn.close()
        logger.debug(f"messaging: recorded {kind} id={row_id}")
        return row_id

    def history(
        self,
        *,
        goal_id: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read recorded messages, newest first."""
        clauses: list[str] = []
        params: list[Any] = []
        if goal_id:
            clauses.append("goal_id=?")
            params.append(goal_id)
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                f"SELECT id, ts, kind, from_agent, to_agent, goal_id, task_id, "
                f"status, payload FROM mesh_messages {where} "
                f"ORDER BY ts DESC, rowid DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
