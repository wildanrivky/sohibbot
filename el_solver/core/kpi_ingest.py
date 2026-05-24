"""KPI ingest + read (R14 M2).

Manual (and later API) ingest of business numbers into ``kpi_snapshots``.
Unit slug is derived from the metric prefix (``myagent.metric`` →
unit ``myagent``) so portfolio roll-ups work without extra wiring.

CLI:
    el kpi log myagent.metric 4 "DP Jepang 5D diterima"
    el kpi show --metric myagent.metric
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_ENSURE_SQL = """
CREATE TABLE IF NOT EXISTS kpi_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  metric TEXT NOT NULL,
  unit TEXT,
  value REAL NOT NULL,
  note TEXT,
  source TEXT DEFAULT 'manual'
);
"""


class KpiError(ValueError):
    """Invalid KPI input (raised at the CLI/system boundary)."""


@dataclass
class KpiPoint:
    id: int
    ts: str
    metric: str
    unit: str | None
    value: float
    note: str | None
    source: str


def unit_of(metric: str) -> str:
    """Unit slug = metric prefix before the first dot."""
    return metric.split(".", 1)[0] if "." in metric else metric


def _ensure(conn: Any) -> None:
    conn.executescript(_ENSURE_SQL)


def log_kpi(
    metric: str,
    value: float | int | str,
    note: str = "",
    *,
    unit: str | None = None,
    source: str = "manual",
    db_path: Path | None = None,
) -> int:
    """Append one KPI snapshot. Returns the new row id.

    Raises ``KpiError`` for empty metric or non-numeric value.
    """
    metric = (metric or "").strip()
    if not metric:
        raise KpiError("metric kosong")
    try:
        fvalue = float(value)
    except (TypeError, ValueError) as exc:
        raise KpiError(f"value bukan angka: {value!r}") from exc

    resolved_unit = unit or unit_of(metric)
    conn = get_connection(db_path)
    try:
        _ensure(conn)
        cur = conn.execute(
            """INSERT INTO kpi_snapshots (metric, unit, value, note, source)
               VALUES (?,?,?,?,?)""",
            (metric, resolved_unit, fvalue, note or None, source),
        )
        conn.commit()
        row_id = int(cur.lastrowid or 0)
    finally:
        conn.close()
    logger.info(f"kpi: logged {metric}={fvalue} (unit={resolved_unit})")
    return row_id


def _rows_to_points(rows: list[Any]) -> list[KpiPoint]:
    return [
        KpiPoint(
            id=r["id"],
            ts=r["ts"],
            metric=r["metric"],
            unit=r["unit"],
            value=r["value"],
            note=r["note"],
            source=r["source"],
        )
        for r in rows
    ]


def recent(
    metric: str | None = None,
    unit: str | None = None,
    limit: int = 20,
    db_path: Path | None = None,
) -> list[KpiPoint]:
    """Snapshots newest-first, optionally filtered by metric or unit."""
    clauses: list[str] = []
    params: list[Any] = []
    if metric:
        clauses.append("metric=?")
        params.append(metric)
    if unit:
        clauses.append("unit=?")
        params.append(unit)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_connection(db_path)
    try:
        _ensure(conn)
        rows = conn.execute(
            f"SELECT id, ts, metric, unit, value, note, source "
            f"FROM kpi_snapshots {where} ORDER BY ts DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()
    return _rows_to_points(rows)


def latest(metric: str, db_path: Path | None = None) -> KpiPoint | None:
    """Most recent snapshot for ``metric`` (or None)."""
    points = recent(metric=metric, limit=1, db_path=db_path)
    return points[0] if points else None


def series(metric: str, db_path: Path | None = None) -> list[tuple[str, float]]:
    """Chronological (ts, value) pairs for trend/causal analysis."""
    conn = get_connection(db_path)
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT ts, value FROM kpi_snapshots WHERE metric=? "
            "ORDER BY ts ASC, id ASC",
            (metric,),
        ).fetchall()
    finally:
        conn.close()
    return [(r["ts"], r["value"]) for r in rows]
