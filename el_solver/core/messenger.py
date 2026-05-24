"""
Agent Messenger — async message passing antar agent via SQLite.

Pattern: Agent A kirim pesan ke Agent B.
Agent B poll unread messages saat dia dipanggil.

Usage:
    messenger = AgentMessenger()
    msg_id = messenger.send("orchestrator", "news-agent", {"task": "summarize"})
    messages = messenger.poll("news-agent")
    messenger.mark_processed(msg_id)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class AgentMessage:
    id: int
    sender: str
    recipient: str
    payload: dict[str, Any]
    status: str
    created_at: str


# ── Messenger ──────────────────────────────────────────────────────────────────

class AgentMessenger:
    """
    Wrapper untuk tabel agent_messages di SQLite.
    Mendukung send, poll, mark_read, mark_processed, dan purge.
    """

    def send(
        self,
        sender: str,
        recipient: str,
        payload: dict[str, Any],
    ) -> int:
        """
        Kirim pesan dari sender ke recipient.
        Return ID pesan yang baru dibuat.
        """
        conn = get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO agent_messages (sender, recipient, payload, status) "
                "VALUES (?, ?, ?, 'unread')",
                (sender, recipient, json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()
            msg_id = cur.lastrowid
            logger.debug(f"messenger: sent msg#{msg_id} {sender}→{recipient}")
            return msg_id
        finally:
            conn.close()

    def poll(self, recipient: str, limit: int = 50) -> list[AgentMessage]:
        """
        Ambil semua unread messages untuk recipient.
        Tidak auto-mark sebagai read — caller yang tentukan.
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, sender, recipient, payload, status, created_at "
                "FROM agent_messages "
                "WHERE recipient=? AND status='unread' "
                "ORDER BY created_at LIMIT ?",
                (recipient, limit),
            ).fetchall()
            return [
                AgentMessage(
                    id=r["id"],
                    sender=r["sender"],
                    recipient=r["recipient"],
                    payload=json.loads(r["payload"]),
                    status=r["status"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def mark_read(self, message_id: int) -> bool:
        """Tandai pesan sebagai read (sudah diterima, belum diproses)."""
        return self._update_status(message_id, "read")

    def mark_processed(self, message_id: int) -> bool:
        """Tandai pesan sebagai processed (sudah selesai diproses)."""
        return self._update_status(message_id, "processed")

    def _update_status(self, message_id: int, status: str) -> bool:
        conn = get_connection()
        try:
            cur = conn.execute(
                "UPDATE agent_messages SET status=? WHERE id=?",
                (status, message_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get(self, message_id: int) -> AgentMessage | None:
        """Ambil satu pesan by ID."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id, sender, recipient, payload, status, created_at "
                "FROM agent_messages WHERE id=?",
                (message_id,),
            ).fetchone()
            if row is None:
                return None
            return AgentMessage(
                id=row["id"],
                sender=row["sender"],
                recipient=row["recipient"],
                payload=json.loads(row["payload"]),
                status=row["status"],
                created_at=row["created_at"],
            )
        finally:
            conn.close()

    def count_unread(self, recipient: str) -> int:
        """Hitung unread messages untuk recipient."""
        conn = get_connection()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM agent_messages WHERE recipient=? AND status='unread'",
                (recipient,),
            ).fetchone()[0]
        finally:
            conn.close()

    def purge_processed(self, recipient: str | None = None) -> int:
        """
        Hapus semua pesan yang sudah di-processed.
        Kalau recipient di-spesifikasikan, hanya hapus milik recipient itu.
        Return jumlah baris yang dihapus.
        """
        conn = get_connection()
        try:
            if recipient:
                cur = conn.execute(
                    "DELETE FROM agent_messages WHERE status='processed' AND recipient=?",
                    (recipient,),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM agent_messages WHERE status='processed'"
                )
            conn.commit()
            deleted = cur.rowcount
            logger.debug(f"messenger: purged {deleted} processed messages")
            return deleted
        finally:
            conn.close()
