"""
Memory Engine — load memory untuk agent + shared memory pool dengan access control.

Tiga sumber memory yang di-compose saat agent run:
1. Global El Solver memory (memory/user/, memory/MEMORY.md)
2. Per-agent memory (agents/<name>/memory/*.md)
3. Shared cross-agent memory (SQLite shared_memory table)

Access control: agent hanya bisa akses shared memory kalau
`memory_scopes` di manifest.yaml menyebut "shared".

Usage:
    engine = MemoryEngine()
    context = engine.load_for_agent("news-summarizer", manifest)
    engine.write_shared("key", {"value": "..."}, scope="global", created_by="news-summarizer")
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter

from el_solver.config import PROJECT_ROOT
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

MEMORY_DIR = PROJECT_ROOT / "memory"
AGENTS_DIR = PROJECT_ROOT / "agents"

# File yang selalu di-load untuk semua agent
_ALWAYS_LOAD_GLOBAL = [
    "MEMORY.md",
    "user/profile.md",
    "user/preferences.md",
]


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """Satu file memory yang sudah di-load."""
    path: str
    name: str
    description: str
    content: str
    source: str  # "global" | "agent" | "shared"


@dataclass
class AgentMemoryContext:
    """Seluruh memory context untuk satu agent run."""
    agent_name: str
    entries: list[MemoryEntry] = field(default_factory=list)

    def to_text(self) -> str:
        """Gabungkan semua entries jadi satu teks untuk dipass ke agent."""
        parts = []
        for e in self.entries:
            parts.append(f"## [{e.source}] {e.name or e.path}\n{e.content}")
        return "\n\n---\n\n".join(parts)

    def by_source(self, source: str) -> list[MemoryEntry]:
        return [e for e in self.entries if e.source == source]


# ── File loader ────────────────────────────────────────────────────────────────

def _load_md_file(path: Path, source: str) -> MemoryEntry | None:
    """Load satu .md file, parse frontmatter, return MemoryEntry."""
    if not path.exists():
        return None
    try:
        post = frontmatter.load(str(path))
        return MemoryEntry(
            path=str(path),
            name=str(post.metadata.get("name", path.stem)),
            description=str(post.metadata.get("description", "")),
            content=post.content.strip(),
            source=source,
        )
    except Exception as e:
        logger.warning(f"memory: gagal load {path}: {e}")
        return None


def _load_global_always() -> list[MemoryEntry]:
    """Load global always-load files."""
    entries: list[MemoryEntry] = []
    for rel_path in _ALWAYS_LOAD_GLOBAL:
        path = MEMORY_DIR / rel_path
        entry = _load_md_file(path, source="global")
        if entry:
            entries.append(entry)
    return entries


def _load_agent_memory(agent_name: str) -> list[MemoryEntry]:
    """Load per-agent memory files dari agents/<name>/memory/."""
    mem_dir = AGENTS_DIR / agent_name / "memory"
    if not mem_dir.exists():
        return []
    entries: list[MemoryEntry] = []
    for md_file in sorted(mem_dir.glob("*.md")):
        entry = _load_md_file(md_file, source="agent")
        if entry:
            entries.append(entry)
    return entries


# ── Shared memory (SQLite) ────────────────────────────────────────────────────

def _load_shared_memory(scope_filter: str | None = None) -> list[MemoryEntry]:
    """Load shared memory entries dari SQLite."""
    conn = get_connection()
    try:
        if scope_filter:
            rows = conn.execute(
                "SELECT key, value, scope, created_by FROM shared_memory WHERE scope=?",
                (scope_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, value, scope, created_by FROM shared_memory"
            ).fetchall()
        entries: list[MemoryEntry] = []
        for r in rows:
            try:
                val = json.loads(r["value"])
                content = json.dumps(val, ensure_ascii=False, indent=2) if isinstance(val, dict) else str(val)
            except (json.JSONDecodeError, TypeError):
                content = str(r["value"])
            entries.append(MemoryEntry(
                path=f"shared:{r['key']}",
                name=r["key"],
                description=f"scope={r['scope']}, by={r['created_by']}",
                content=content,
                source="shared",
            ))
        return entries
    finally:
        conn.close()


# ── Memory Engine ─────────────────────────────────────────────────────────────

class MemoryEngine:
    """
    Load dan compose memory context untuk agent.
    Write shared memory dengan access control.
    """

    def load_for_agent(
        self,
        agent_name: str,
        manifest: dict[str, Any] | None = None,
    ) -> AgentMemoryContext:
        """
        Load seluruh memory context untuk agent.

        Args:
            agent_name : nama agent
            manifest   : manifest.yaml dict (untuk cek memory_scopes)

        Returns:
            AgentMemoryContext dengan semua entries ter-load
        """
        entries: list[MemoryEntry] = []

        # 1. Global always-load
        entries.extend(_load_global_always())

        # 2. Per-agent memory
        entries.extend(_load_agent_memory(agent_name))

        # 3. Shared memory (hanya kalau diizinkan di manifest)
        if self._can_access_shared(agent_name, manifest):
            shared = _load_shared_memory()
            entries.extend(shared)
            logger.debug(f"memory[{agent_name}]: loaded {len(shared)} shared entries")

        logger.debug(
            f"memory[{agent_name}]: {len(entries)} entries "
            f"(global={len([e for e in entries if e.source == 'global'])}, "
            f"agent={len([e for e in entries if e.source == 'agent'])}, "
            f"shared={len([e for e in entries if e.source == 'shared'])})"
        )
        return AgentMemoryContext(agent_name=agent_name, entries=entries)

    def _can_access_shared(
        self,
        agent_name: str,
        manifest: dict[str, Any] | None,
    ) -> bool:
        """Cek apakah agent boleh akses shared memory."""
        if manifest is None:
            return False
        memory_config = manifest.get("memory", {})
        scopes = memory_config.get("scopes", [])
        return "shared" in scopes

    def write_shared(
        self,
        key: str,
        value: Any,
        scope: str = "global",
        created_by: str = "el-solver",
        ttl_days: int | None = None,
    ) -> None:
        """
        Tulis atau update satu shared memory entry.
        Access control: hanya El Solver core atau agent dengan scope "shared".
        """
        conn = get_connection()
        try:
            value_json = json.dumps(value, ensure_ascii=False)
            conn.execute(
                """INSERT INTO shared_memory (key, value, scope, created_by, ttl_days,
                   updated_at)
                   VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(key) DO UPDATE SET
                     value=excluded.value,
                     scope=excluded.scope,
                     created_by=excluded.created_by,
                     ttl_days=excluded.ttl_days,
                     updated_at=CURRENT_TIMESTAMP""",
                (key, value_json, scope, created_by, ttl_days),
            )
            conn.commit()
            logger.debug(f"memory: write_shared key='{key}' scope={scope}")
        finally:
            conn.close()

    def read_shared(self, key: str) -> Any | None:
        """Baca satu shared memory entry by key."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM shared_memory WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["value"])
        finally:
            conn.close()

    def delete_shared(self, key: str) -> bool:
        """Hapus satu shared memory entry. Return True kalau berhasil."""
        conn = get_connection()
        try:
            cur = conn.execute("DELETE FROM shared_memory WHERE key=?", (key,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def list_shared_keys(self, scope: str | None = None) -> list[str]:
        """List semua key di shared memory, optionally filter by scope."""
        conn = get_connection()
        try:
            if scope:
                rows = conn.execute(
                    "SELECT key FROM shared_memory WHERE scope=? ORDER BY key",
                    (scope,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT key FROM shared_memory ORDER BY key"
                ).fetchall()
            return [r["key"] for r in rows]
        finally:
            conn.close()

    def purge_expired(self) -> int:
        """Hapus shared memory entries yang sudah expired (ttl_days terlewat)."""
        conn = get_connection()
        try:
            cur = conn.execute(
                """DELETE FROM shared_memory
                   WHERE ttl_days IS NOT NULL
                   AND julianday('now') - julianday(created_at) > ttl_days"""
            )
            conn.commit()
            n = cur.rowcount
            if n > 0:
                logger.info(f"memory: purged {n} expired shared entries")
            return n
        finally:
            conn.close()
