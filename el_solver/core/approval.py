"""
Approval Gate — kirim permintaan approval ke Telegram dan tunggu jawaban Wildan.

Flow:
1. Sistem deteksi plan/action dengan risk L2
2. approval.request_approval(context, risk_result) dipanggil
3. Telegram inline keyboard dikirim ke owner (✅ Setuju / ❌ Tolak)
4. Polling status via file-based state (async-safe)
5. Return ApprovalResult (approved / rejected / timeout)

Design constraints:
- Tidak import telegram bot di sini — approval.py hanya tahu cara menulis
  ke file state dan membacanya. Bot yang handle callback.
- Sync-friendly: polling via asyncio.sleep kalau dipanggil dari async context,
  atau threading.Event dari sync context.
- Timeout default 5 menit.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from el_solver.config import PROJECT_ROOT, settings
from el_solver.core.risk import RiskResult
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

# Direktori state file approval (satu file per pending request)
APPROVAL_DIR = PROJECT_ROOT / "data" / "approvals"

# Default timeout dalam detik
DEFAULT_TIMEOUT = 300  # 5 menit


# ── Status enum ────────────────────────────────────────────────────────────────

class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"   # untuk L0/L1 yang tidak butuh approval


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class ApprovalRequest:
    request_id: str
    context: str                        # deskripsi aksi yang butuh approval
    risk_level: str                     # "L2" atau "L3"
    reasons: list[str]
    created_at: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None   # "wildan" atau "timeout"
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "context": self.context,
            "risk_level": self.risk_level,
            "reasons": self.reasons,
            "created_at": self.created_at,
            "status": self.status.value,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ApprovalRequest":
        return cls(
            request_id=d["request_id"],
            context=d["context"],
            risk_level=d["risk_level"],
            reasons=d.get("reasons", []),
            created_at=d["created_at"],
            status=ApprovalStatus(d.get("status", "pending")),
            decided_at=d.get("decided_at"),
            decided_by=d.get("decided_by"),
            note=d.get("note"),
        )

    def state_path(self, approval_dir: Path = APPROVAL_DIR) -> Path:
        return approval_dir / f"{self.request_id}.json"

    def save(self, approval_dir: Path = APPROVAL_DIR) -> None:
        approval_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_path(approval_dir)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    def refresh(self, approval_dir: Path = APPROVAL_DIR) -> "ApprovalRequest":
        """Baca ulang dari disk — return versi terbaru."""
        path = self.state_path(approval_dir)
        if not path.exists():
            return self
        data = json.loads(path.read_text())
        return ApprovalRequest.from_dict(data)


@dataclass
class ApprovalResult:
    status: ApprovalStatus
    request_id: str
    context: str
    risk_level: str
    decided_at: Optional[str] = None
    note: Optional[str] = None

    @property
    def approved(self) -> bool:
        return self.status == ApprovalStatus.APPROVED

    @property
    def rejected(self) -> bool:
        return self.status in (ApprovalStatus.REJECTED, ApprovalStatus.TIMEOUT)

    @property
    def skipped(self) -> bool:
        return self.status == ApprovalStatus.SKIPPED


# ── Telegram message builder ───────────────────────────────────────────────────

def build_approval_message(req: ApprovalRequest) -> tuple[str, list]:
    """
    Return (message_text, inline_keyboard) untuk dikirim via Telegram bot.

    inline_keyboard format sesuai python-telegram-bot InlineKeyboardButton.
    """
    reasons_text = "\n".join(f"  • {r}" for r in req.reasons) if req.reasons else "  • (tidak ada detail)"
    timestamp = req.created_at[:19].replace("T", " ")

    text = (
        f"⚠️ *Permintaan Approval*\n\n"
        f"*Aksi:* {req.context}\n"
        f"*Risk:* `{req.risk_level}`\n\n"
        f"*Alasan flagged:*\n{reasons_text}\n\n"
        f"_ID: `{req.request_id[:8]}`_ — {timestamp} UTC"
    )

    # Callback data format: "approve:<request_id>" atau "reject:<request_id>"
    keyboard = [
        [
            {"text": "✅ Setuju", "callback_data": f"approve:{req.request_id}"},
            {"text": "❌ Tolak", "callback_data": f"reject:{req.request_id}"},
        ]
    ]

    return text, keyboard


# ── State file operations ──────────────────────────────────────────────────────

def create_request(
    context: str,
    risk_result: RiskResult,
    approval_dir: Path = APPROVAL_DIR,
) -> ApprovalRequest:
    """Buat ApprovalRequest baru dan simpan ke disk."""
    req = ApprovalRequest(
        request_id=str(uuid.uuid4()),
        context=context,
        risk_level=risk_result.level,
        reasons=list(risk_result.reasons),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    req.save(approval_dir)
    logger.info(f"approval: created request {req.request_id[:8]} level={req.risk_level}")
    return req


def decide(
    request_id: str,
    approved: bool,
    decided_by: str = "wildan",
    note: Optional[str] = None,
    approval_dir: Path = APPROVAL_DIR,
) -> ApprovalRequest:
    """
    Catat keputusan approval (dipanggil dari Telegram bot callback handler).

    Args:
        request_id : ID lengkap dari request
        approved   : True = setuju, False = tolak
        decided_by : siapa yang memutuskan (default "wildan")
        note       : catatan opsional dari user
    """
    path = approval_dir / f"{request_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Approval request tidak ditemukan: {request_id}")

    req = ApprovalRequest.from_dict(json.loads(path.read_text()))
    if req.status != ApprovalStatus.PENDING:
        raise ValueError(f"Request {request_id[:8]} sudah diputuskan: {req.status}")

    req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    req.decided_at = datetime.now(timezone.utc).isoformat()
    req.decided_by = decided_by
    req.note = note
    req.save(approval_dir)

    action = "approved" if approved else "rejected"
    logger.info(f"approval: {action} {request_id[:8]} by {decided_by}")
    return req


def get_pending(approval_dir: Path = APPROVAL_DIR) -> list[ApprovalRequest]:
    """Return semua ApprovalRequest yang masih PENDING."""
    if not approval_dir.exists():
        return []
    pending = []
    for f in approval_dir.glob("*.json"):
        try:
            req = ApprovalRequest.from_dict(json.loads(f.read_text()))
            if req.status == ApprovalStatus.PENDING:
                pending.append(req)
        except Exception:
            continue
    return sorted(pending, key=lambda r: r.created_at)


# ── Async polling gate ─────────────────────────────────────────────────────────

async def wait_for_approval(
    req: ApprovalRequest,
    timeout: float = DEFAULT_TIMEOUT,
    poll_interval: float = 2.0,
    approval_dir: Path = APPROVAL_DIR,
) -> ApprovalResult:
    """
    Poll state file sampai request diputuskan atau timeout.

    Dipanggil dari async context (orchestrator/pipeline).
    """
    deadline = time.monotonic() + timeout
    logger.info(f"approval: waiting for {req.request_id[:8]} (timeout={timeout}s)")

    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        current = req.refresh(approval_dir)
        if current.status != ApprovalStatus.PENDING:
            return ApprovalResult(
                status=current.status,
                request_id=current.request_id,
                context=current.context,
                risk_level=current.risk_level,
                decided_at=current.decided_at,
                note=current.note,
            )

    # Timeout — mark as timeout
    req_latest = req.refresh(approval_dir)
    if req_latest.status == ApprovalStatus.PENDING:
        req_latest.status = ApprovalStatus.TIMEOUT
        req_latest.decided_at = datetime.now(timezone.utc).isoformat()
        req_latest.decided_by = "timeout"
        req_latest.save(approval_dir)
        logger.warning(f"approval: timeout {req.request_id[:8]}")

    return ApprovalResult(
        status=ApprovalStatus.TIMEOUT,
        request_id=req.request_id,
        context=req.context,
        risk_level=req.risk_level,
        decided_at=req_latest.decided_at,
    )


# ── High-level public API ──────────────────────────────────────────────────────

def needs_approval(risk_result: RiskResult) -> bool:
    """Cek apakah risk result membutuhkan approval Wildan."""
    return risk_result.requires_approval


async def request_approval(
    context: str,
    risk_result: RiskResult,
    send_telegram_fn=None,
    timeout: float = DEFAULT_TIMEOUT,
    approval_dir: Path = APPROVAL_DIR,
) -> ApprovalResult:
    """
    High-level entry point: buat request, kirim Telegram, tunggu jawaban.

    Args:
        context        : deskripsi aksi yang butuh approval
        risk_result    : hasil dari risk.assess_*
        send_telegram_fn : callable async (text, keyboard) → None
                           (opsional — kalau None, hanya tulis state file)
        timeout        : detik tunggu jawaban
        approval_dir   : override direktori state
    """
    if not needs_approval(risk_result):
        return ApprovalResult(
            status=ApprovalStatus.SKIPPED,
            request_id="",
            context=context,
            risk_level=risk_result.level,
        )

    req = create_request(context, risk_result, approval_dir)
    text, keyboard = build_approval_message(req)

    if send_telegram_fn is not None:
        try:
            await send_telegram_fn(text, keyboard)
        except Exception as e:
            logger.error(f"approval: gagal kirim Telegram: {e}")

    return await wait_for_approval(req, timeout=timeout, approval_dir=approval_dir)
