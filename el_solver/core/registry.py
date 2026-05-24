"""
Agent Registry — CRUD untuk agents_registry SQLite table.

Registry menyimpan semua agent yang sudah di-materialize oleh factory:
metadata, status, archetype, dan manifest JSON.

Usage:
    registry = AgentRegistry()
    registry.register("news-summarizer", "scheduled", "Rangkum berita", manifest)
    agent = registry.get("news-summarizer")
    all_agents = registry.list_active()
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class AgentRecord:
    name: str
    archetype: str
    role_description: str
    status: str
    created_at: str
    manifest: dict[str, Any]

    @property
    def is_active(self) -> bool:
        return self.status == "active"


# ── Registry ──────────────────────────────────────────────────────────────────

class AgentRegistry:
    """CRUD wrapper untuk tabel agents_registry di SQLite."""

    def register(
        self,
        name: str,
        archetype: str,
        role_description: str,
        manifest: dict[str, Any],
        overwrite: bool = False,
    ) -> None:
        """
        Daftarkan agent ke registry.
        Raises ValueError kalau agent sudah ada dan overwrite=False.
        """
        conn = get_connection()
        try:
            existing = self.get(name)
            if existing and not overwrite:
                raise ValueError(
                    f"Agent '{name}' sudah terdaftar. "
                    "Gunakan overwrite=True untuk update."
                )
            manifest_json = json.dumps(manifest, ensure_ascii=False)
            if existing and overwrite:
                conn.execute(
                    """UPDATE agents_registry
                       SET archetype=?, role_description=?, status='active',
                           manifest=?
                       WHERE name=?""",
                    (archetype, role_description, manifest_json, name),
                )
            else:
                conn.execute(
                    """INSERT INTO agents_registry
                       (name, archetype, role_description, status, manifest)
                       VALUES (?, ?, ?, 'active', ?)""",
                    (name, archetype, role_description, manifest_json),
                )
            conn.commit()
            logger.info(f"registry: registered agent '{name}' ({archetype})")
        finally:
            conn.close()

    def get(self, name: str) -> AgentRecord | None:
        """Ambil satu agent dari registry. Return None kalau tidak ada."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT name, archetype, role_description, status, created_at, manifest "
                "FROM agents_registry WHERE name=?",
                (name,),
            ).fetchone()
            if row is None:
                return None
            return AgentRecord(
                name=row["name"],
                archetype=row["archetype"],
                role_description=row["role_description"] or "",
                status=row["status"],
                created_at=row["created_at"],
                manifest=json.loads(row["manifest"] or "{}"),
            )
        finally:
            conn.close()

    def list_all(self) -> list[AgentRecord]:
        """List semua agent (termasuk deprecated/retired)."""
        return self._list_by_status(None)

    def list_active(self) -> list[AgentRecord]:
        """List agent dengan status active saja."""
        return self._list_by_status("active")

    def _list_by_status(self, status: str | None) -> list[AgentRecord]:
        conn = get_connection()
        try:
            if status:
                rows = conn.execute(
                    "SELECT name, archetype, role_description, status, created_at, manifest "
                    "FROM agents_registry WHERE status=? ORDER BY created_at",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT name, archetype, role_description, status, created_at, manifest "
                    "FROM agents_registry ORDER BY created_at"
                ).fetchall()
            return [
                AgentRecord(
                    name=r["name"],
                    archetype=r["archetype"],
                    role_description=r["role_description"] or "",
                    status=r["status"],
                    created_at=r["created_at"],
                    manifest=json.loads(r["manifest"] or "{}"),
                )
                for r in rows
            ]
        finally:
            conn.close()

    def update_status(self, name: str, status: str) -> bool:
        """
        Update status agent: active | deprecated | retired.
        Return True kalau berhasil, False kalau agent tidak ditemukan.
        """
        valid = {"active", "deprecated", "retired"}
        if status not in valid:
            raise ValueError(f"status harus salah satu dari {valid}")
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE agents_registry SET status=? WHERE name=?",
                (status, name),
            )
            conn.commit()
            updated = cur.rowcount > 0
            if updated:
                logger.info(f"registry: agent '{name}' status → {status}")
            return updated
        finally:
            conn.close()

    def deregister(self, name: str) -> bool:
        """
        Hapus agent dari registry (hard delete).
        Return True kalau berhasil.
        """
        conn = get_connection()
        try:
            cur = conn.execute("DELETE FROM agents_registry WHERE name=?", (name,))
            conn.commit()
            deleted = cur.rowcount > 0
            if deleted:
                logger.info(f"registry: deregistered agent '{name}'")
            return deleted
        finally:
            conn.close()

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def count(self) -> int:
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM agents_registry"
            ).fetchone()[0]
        finally:
            conn.close()


def sync_agents_to_registry(
    agents_dir: Path | None = None,
    db_path: Path | None = None,
) -> list[str]:
    """Reflect agent manifests into ``agents_registry`` (R13 M4).

    Upserts name/archetype/role/status/manifest/capabilities for every agent
    found under ``agents_dir`` so the capability graph (which reads the
    ``capabilities`` column) stays in sync with the source-of-truth manifests.
    Returns the list of synced agent names.
    """
    from el_solver.agents.base import scan_agents
    from el_solver.config import PROJECT_ROOT

    base_dir = agents_dir or (PROJECT_ROOT / "agents")
    synced: list[str] = []
    conn = get_connection(db_path)
    try:
        for info in scan_agents(base_dir):
            caps_json = json.dumps(info.capabilities, ensure_ascii=False)
            manifest_json = json.dumps(
                {
                    "name": info.name,
                    "archetype": info.archetype,
                    "role": info.role,
                    "capabilities": info.capabilities,
                },
                ensure_ascii=False,
            )
            exists = conn.execute(
                "SELECT 1 FROM agents_registry WHERE name=?", (info.name,)
            ).fetchone()
            if exists:
                conn.execute(
                    """UPDATE agents_registry
                       SET archetype=?, role_description=?, status=?,
                           manifest=?, capabilities=?
                       WHERE name=?""",
                    (
                        info.archetype,
                        info.role,
                        info.status,
                        manifest_json,
                        caps_json,
                        info.name,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO agents_registry
                       (name, archetype, role_description, status,
                        manifest, capabilities)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        info.name,
                        info.archetype,
                        info.role,
                        info.status,
                        manifest_json,
                        caps_json,
                    ),
                )
            synced.append(info.name)
        conn.commit()
    finally:
        conn.close()
    logger.info(f"registry: synced {len(synced)} agents from manifests")
    return synced
