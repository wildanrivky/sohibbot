"""Web channel wrapper — proses pesan dari browser dashboard.

Pattern mengikuti telegram_bot.handle_message():
load context → classify → handle → persist turn.

Channel ID = "telegram" sengaja agar history share dengan Telegram bot.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

# In-memory store untuk pending plans (single-user, tidak perlu Redis)
_pending_plans: dict[str, Any] = {}

# ── WA shortcuts (mirror dari claude_telegram.py) ─────────────────────────────
_WA_QUERY_PATTERN = re.compile(r"ada pesan.*wa|pesan.*wa|wa.*pesan|pesan masuk|cek wa", re.IGNORECASE)
_BALES_PATTERN = re.compile(r"^bal(?:as|es?)\s+(.+?):\s*(.+)$", re.IGNORECASE | re.DOTALL)
_CONFIRM_WORD = r"(?:iya|ya|ok|oke|bener|benar|betul|kirim|send|gas|lanjut|yep|yup|yoi|siap|boleh|setuju|yes|dong|deh|lah)"
_CONFIRM_PATTERN = re.compile(
    r"^" + _CONFIRM_WORD + r"(?:[\s!.,]+" + _CONFIRM_WORD + r")*[\s!.,]*$",
    re.IGNORECASE,
)
_CANCEL_PATTERN = re.compile(r"^(batal|cancel|gak jadi|nggak jadi|ga jadi)[\s!.]*$", re.IGNORECASE)
_GANTI_TRIGGER = re.compile(r"^(?:bukan|ganti|ubah|koreksi|salah)\b", re.IGNORECASE)
_STRIP_INSTRUCTIONS = re.compile(r"^(?:bukan|ganti|ubah|koreksi|salah|jadi)[,\s:]*", re.IGNORECASE)
_PILIH_PATTERN = re.compile(r"^(?:yang\s+)?(?:nomor\s+|no\s*\.?\s*)?(\d+)$|^yang\s+(\w+)$", re.IGNORECASE)
_KIRIM_PATTERN = re.compile(r"^(?:kirim|send)\s+(?:wa|whatsapp)\s+ke\s+(.+?):\s*(.+)$", re.IGNORECASE | re.DOTALL)
_KIRIM_NOPESAN_PATTERN = re.compile(r"^(?:kirim|send)\s+(?:wa|whatsapp)\s+ke\s+(.+)$", re.IGNORECASE)
_GRUP_INACTIVE_PATTERN = re.compile(
    r"grup\b.{0,25}(?:tidak\s+aktif|sepi|jarang|lama\s+tidak|mati)|"
    r"cari\s+grup|"
    r"mau\s+leave|"
    r"leave\s+grup|"
    r"tinggalkan\s+grup|"
    r"keluar\s+dari\s+grup",
    re.IGNORECASE,
)
_TAMPILKAN_SEMUA_PATTERN = re.compile(r"^tampilkan\s+semua|^semua$|^lihat\s+semua|^tampilkan\s+list", re.IGNORECASE)
_LEAVE_PATTERN = re.compile(
    r"^(?:leave|keluar|tinggalkan)\s+(?:dari\s+)?(?:grup\s+)?(?:nomor\s+|no\s*\.?\s*)?(\d+)$|"
    r"^(?:leave|keluar|tinggalkan)\s+(?:dari\s+)?(?:grup\s+)?(.+)$",
    re.IGNORECASE,
)

# Pending WA send — disimpan ke file supaya survive uvicorn --reload
_WA_API_URL = "http://127.0.0.1:3001/send"
_PENDING_WA_FILE = Path("/tmp/el_solver_wa_pending.json")
_PENDING_GRUP_FILE = Path("/tmp/el_solver_grup_pending.json")


def _load_pending_wa() -> dict | None:
    try:
        if _PENDING_WA_FILE.exists():
            return json.loads(_PENDING_WA_FILE.read_text())
    except Exception:
        pass
    return None


def _save_pending_wa(state: dict | None) -> None:
    try:
        if state is None:
            _PENDING_WA_FILE.unlink(missing_ok=True)
        else:
            _PENDING_WA_FILE.write_text(json.dumps(state))
    except Exception:
        pass
_WA_TASKS_DIR = Path(__file__).parent.parent.parent / "memory" / "tasks"
_CATEGORY_EMOJI = {"penting": "🔴", "tanya": "🟡", "info": "🟢", "santai": "⚪", "belum dikategorikan": "⬜", "follow-up belum dibalas": "🔔"}


def _time_ago(dt: datetime) -> str:
    diff = datetime.now() - dt
    hours = int(diff.total_seconds() / 3600)
    if hours < 24:
        return f"{hours} jam lalu"
    return f"{hours // 24} hari lalu"


_FOLLOW_UP_CONTEXT_RE = re.compile(
    r"""
    \b(
        tadi|
        barusan|
        sebelumnya|
        yang\s+tadi|
        yang\s+barusan|
        lanjut|
        lanjutin|
        lanjutkan|
        itu|
        ini\s+itu|
        maksudnya|
        konteks|
        sebelumnya\s+itu|
        apa\s+tadi|
        saya\s+ngomong\s+apa|
        aku\s+ngomong\s+apa|
        apa\s+yang\s+(?:tadi|barusan)\s+saya\s+bilang|
        apa\s+yang\s+(?:tadi|barusan)\s+aku\s+bilang
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _should_include_conversation_context(text: str) -> bool:
    """Return True when the message looks like a follow-up where prior convo matters."""
    t = (text or "").strip()
    if not t:
        return False
    if _FOLLOW_UP_CONTEXT_RE.search(t):
        return True
    return False


def _get_wa_messages() -> tuple[str, str]:
    """Return (display_text, tts_text) — display pakai format, tts natural speech."""
    files = sorted(_WA_TASKS_DIR.glob("wa-*.md"), key=lambda f: f.stat().st_mtime)
    if not files:
        return "Tidak ada pesan WA yang tersimpan.", "Tidak ada pesan WA nih."

    items = []
    for f in files:
        content = f.read_text(encoding="utf-8")
        kat_m = re.search(r"^Kategori:\s*(.+)$", content, re.MULTILINE)
        pesan_m = re.search(r"^Pesan:\s*(.+)$", content, re.MULTILINE)
        dari_m = re.search(r"^Dari:\s*(.+)$", content, re.MULTILINE)
        kategori = kat_m.group(1).strip() if kat_m else "belum dikategorikan"
        pesan = pesan_m.group(1).strip() if pesan_m else "(kosong)"
        dari = dari_m.group(1).strip() if dari_m else "?"
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        items.append({"kategori": kategori, "pesan": pesan, "dari": dari, "waktu": _time_ago(mtime), "mtime": mtime})

    # Sort by priority: penting first
    priority = {"penting": 0, "tanya": 1, "info": 2, "santai": 3}
    items.sort(key=lambda x: (priority.get(x["kategori"], 4), x["mtime"]))

    total = len(items)
    emoji_map = _CATEGORY_EMOJI

    # Display text (untuk layar)
    display_lines = [f"Ada {total} pesan WA belum dibalas:\n"]
    for it in items:
        emoji = emoji_map.get(it["kategori"], "⬜")
        display_lines.append(f"{emoji} {it['kategori'].capitalize()} — {it['waktu']}")
        display_lines.append(f"   Dari: {it['dari']}")
        display_lines.append(f"   {it['pesan']}\n")
    display_text = "\n".join(display_lines)
    display_text += "\nMau balas yang mana dulu?"

    # TTS text (natural speech)
    if total == 1:
        it = items[0]
        tts = f"Ada satu pesan nih, dari {it['dari']}, {it['waktu']}, dia {'bilang' if it['kategori'] in ('info','santai') else 'nanya'} \"{it['pesan']}\". Mau dibalas sekarang?"
    else:
        parts = []
        for i, it in enumerate(items):
            verb = "nanya" if it["kategori"] == "tanya" else "bilang"
            urgency = "yang penting " if it["kategori"] == "penting" else ""
            parts.append(f"{urgency}dari {it['dari']} {it['waktu']} {verb} \"{it['pesan']}\"")
        tts = f"Ada {total} pesan nih. "
        tts += ", ".join(parts[:-1])
        if len(parts) > 1:
            tts += f", sama {parts[-1]}. "
        tts += "Mau balas yang mana dulu?"

    return display_text, tts


def _find_chat_id(nama: str) -> tuple[str | None, str | None]:
    for f in sorted(_WA_TASKS_DIR.glob("wa-*.md"), key=lambda f: f.stat().st_mtime, reverse=True):
        content = f.read_text(encoding="utf-8")
        dari_m = re.search(r"^Dari:\s*(.+)$", content, re.MULTILINE)
        chatid_m = re.search(r"^ChatId:\s*(.+)$", content, re.MULTILINE)
        if dari_m and chatid_m:
            dari = dari_m.group(1).strip()
            if nama.lower() in dari.lower():
                return chatid_m.group(1).strip(), dari
    return None, None


_WA_LOADING = "WA_LOADING"  # sentinel: kontak sedang di-cache


def _search_contacts(query: str) -> list[dict] | str:
    """Cari kontak WA lewat WA bot API. Return list atau _WA_LOADING kalau cache belum siap."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:3001/contacts?q={urllib.parse.quote(query)}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
            if data.get("loading"):
                return _WA_LOADING
            return data.get("results", [])
    except Exception as e:
        logger.error(f"WA contacts search error: {e}")
        return []


def _load_pending_grup() -> dict | None:
    try:
        if _PENDING_GRUP_FILE.exists():
            return json.loads(_PENDING_GRUP_FILE.read_text())
    except Exception:
        pass
    return None


def _save_pending_grup(state: dict | None) -> None:
    try:
        if state is None:
            _PENDING_GRUP_FILE.unlink(missing_ok=True)
        else:
            _PENDING_GRUP_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def _get_inactive_groups(days: int = 30) -> list[dict]:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:3001/groups/inactive?days={days}",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode()).get("results", [])
    except Exception as e:
        logger.error(f"WA groups/inactive error: {e}")
        return []


def _leave_wa_group(group_id: str) -> bool:
    try:
        payload = json.dumps({"chatId": group_id}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3001/groups/leave",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"WA leave group error: {e}")
        return False


def _format_inactive_groups(groups: list[dict], days: int = 30) -> str:
    if not groups:
        return f"Tidak ada grup yang tidak aktif lebih dari {days} hari. Semua masih ada aktivitasnya."
    lines = [f"Ada {len(groups)} grup yang tidak aktif > {days} hari:\n"]
    for i, g in enumerate(groups, 1):
        days_ago = g.get("lastActivityDays")
        days_str = f"{days_ago} hari lalu" if days_ago is not None else "belum pernah ada pesan"
        member_str = f"{g['participantCount']} member" if g.get("participantCount") else ""
        last_msg = g.get("lastMessageText") or ""
        line = f'{i}. {g["name"]}'
        if member_str:
            line += f" ({member_str})"
        line += f" — {days_str}"
        if last_msg:
            preview = last_msg[:60] + "..." if len(last_msg) > 60 else last_msg
            line += f'\n   "{preview}"'
        lines.append(line)
    lines.append('\nMau leave yang mana? Ketik "leave [nomor]" atau "leave [nama grup]".')
    return "\n".join(lines)


def _send_wa_message(chat_id: str, message: str) -> bool:
    try:
        payload = json.dumps({"chatId": chat_id, "message": message}).encode()
        req = urllib.request.Request(_WA_API_URL, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"WA API error: {e}")
        return False


async def process_web_message(message: str) -> dict:
    """Proses satu pesan dari web chat. Return dict siap di-serialize ke JSON."""
    # ── WA shortcuts (bypass Claude) ──────────────────────────────────────────
    # State disimpan ke /tmp supaya survive uvicorn --reload
    pw = _load_pending_wa()

    # Kalau ada perintah baru (kirim/cek WA), reset pending state supaya tidak stuck
    _is_new_wa_cmd = (
        _KIRIM_PATTERN.match(message)
        or _KIRIM_NOPESAN_PATTERN.match(message)
        or _BALES_PATTERN.match(message)
        or _WA_QUERY_PATTERN.search(message)
    )
    if pw is not None and _is_new_wa_cmd:
        _save_pending_wa(None)
        pw = None

    # ── Grup tidak aktif ──────────────────────────────────────────────────────
    pg = _load_pending_grup()

    # Perintah grup baru → reset state
    if pg is not None and _GRUP_INACTIVE_PATTERN.search(message):
        _save_pending_grup(None)
        pg = None

    def _ret(text: str) -> dict:
        return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

    if pg is not None:
        groups = pg.get("groups", [])
        msg_s = message.strip()

        if pg.get("waiting_confirm"):
            target = pg["target"]
            if _CONFIRM_PATTERN.match(msg_s):
                ok = _leave_wa_group(target["id"])
                _save_pending_grup(None)
                nama = target["name"]
                text = f"Sudah keluar dari grup *{nama}*." if ok else f"Gagal leave dari *{nama}* — pastikan WA bot sedang jalan."
                return _ret(text)
            elif _CANCEL_PATTERN.match(msg_s):
                _save_pending_grup(None)
                return _ret("Oke, dibatalkan.")
            else:
                return _ret(f'Leave dari grup *{target["name"]}*? Ketik "iya" atau "batal".')

        if _TAMPILKAN_SEMUA_PATTERN.match(msg_s):
            text = _format_inactive_groups(groups, pg.get("days", 30))
            return _ret(text)

        leave_m = _LEAVE_PATTERN.match(msg_s)
        if leave_m:
            n_str = leave_m.group(1)
            name_str = leave_m.group(2)
            target = None
            if n_str and n_str.isdigit():
                idx = int(n_str) - 1
                if 0 <= idx < len(groups):
                    target = groups[idx]
            elif name_str:
                for g in groups:
                    if name_str.strip().lower() in g["name"].lower():
                        target = g
                        break
            if target:
                pg["waiting_confirm"] = True
                pg["target"] = target
                _save_pending_grup(pg)
                return _ret(f'Yakin mau leave dari grup *{target["name"]}*? Ini tidak bisa dibatalkan ya. (iya/batal)')
            return _ret(f'Tidak ketemu. Ketik nomor 1–{len(groups)} atau nama grupnya.')

        if _CANCEL_PATTERN.match(msg_s):
            _save_pending_grup(None)
            return _ret("Oke, dibatalkan.")

        return _ret('Mau leave yang mana? Ketik "leave [nomor]" atau "batal".')

    if _GRUP_INACTIVE_PATTERN.search(message):
        days_m = re.search(r'(\d+)\s*hari', message)
        days = int(days_m.group(1)) if days_m else 30
        groups = _get_inactive_groups(days)
        _save_pending_grup({"groups": groups, "days": days})
        text = _format_inactive_groups(groups, days)
        return _ret(text)

    # Handle konfirmasi / koreksi / batal / pilih kontak untuk pending WA send
    if pw is not None:
        msg_stripped = message.strip()

        # Pilih dari daftar kontak kalau ada candidates
        if pw.get("candidates"):
            candidates = pw["candidates"]
            ordinals = {"pertama": 0, "satu": 0, "kedua": 1, "dua": 1, "ketiga": 2, "tiga": 2,
                        "keempat": 3, "empat": 3, "kelima": 4, "lima": 4}
            idx = None
            pilih_m = _PILIH_PATTERN.match(msg_stripped)
            if pilih_m:
                n = pilih_m.group(1) or pilih_m.group(2)
                if n and n.isdigit():
                    idx = int(n) - 1
                elif n and n.lower() in ordinals:
                    idx = ordinals[n.lower()]
            # Fallback: cari angka atau kata ordinal di mana saja dalam pesan
            if idx is None:
                num_m = re.search(r'\b(\d+)\b', msg_stripped)
                if num_m:
                    idx = int(num_m.group(1)) - 1
            if idx is None:
                for word, i in ordinals.items():
                    if re.search(r'\b' + re.escape(word) + r'\b', msg_stripped, re.IGNORECASE):
                        idx = i
                        break
            if idx is None:
                for i, c in enumerate(candidates):
                    if msg_stripped.lower() in c["name"].lower():
                        idx = i
                        break
            if idx is not None and 0 <= idx < len(candidates):
                chosen = candidates[idx]
                pw["chat_id"] = chosen["id"]
                pw["dari"] = chosen["name"]
                del pw["candidates"]
                _save_pending_wa(pw)
                if pw.get("pesan") is None:
                    text = f'Oke, {chosen["name"]}. Mau kirim pesan apa?'
                else:
                    text = f'Oke, mau kirim ke {chosen["name"]}: "{pw["pesan"]}". Bener?'
                return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}
            text = f'Hmm, pilih nomor 1–{len(candidates)} atau ketik nama kontaknya.'
            return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

        # Stage: kontak sudah dipilih, tapi belum ada pesan → pesan berikutnya adalah isi pesan
        if pw.get("pesan") is None and not _CANCEL_PATTERN.match(msg_stripped):
            # Normalisasi whitespace/newline yang tidak sengaja masuk
            pesan_clean = re.sub(r'\s+', ' ', msg_stripped).strip()
            pw["pesan"] = pesan_clean
            _save_pending_wa(pw)
            dari = pw["dari"]
            text = f'Mau kirim ke {dari}: "{pesan_clean}". Bener?'
            return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

        if _CONFIRM_PATTERN.match(msg_stripped):
            ok = _send_wa_message(pw["chat_id"], pw["pesan"])
            dari, pesan = pw["dari"], pw["pesan"]
            _save_pending_wa(None)
            text = f"Oke, terkirim ke {dari}! ✅" if ok else "Gagal kirim — pastikan WA bot sedang jalan."
            tts = "Oke, terkirim!" if ok else "Gagal kirim."
            return {"text": text, "tts_text": tts, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

        if _GANTI_TRIGGER.match(msg_stripped):
            cleaned = msg_stripped
            while _STRIP_INSTRUCTIONS.match(cleaned):
                cleaned = _STRIP_INSTRUCTIONS.sub("", cleaned).strip()
            if cleaned:
                # Ada pesan baru setelah kata koreksi
                pw["pesan"] = re.sub(r'\s+', ' ', cleaned).strip()
                _save_pending_wa(pw)
                dari, pesan = pw["dari"], pw["pesan"]
                text = f'Oke diubah. Mau kirim ke {dari}: "{pesan}". Bener sekarang?'
                return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}
            else:
                # "salah" saja tanpa pesan baru → reset pesan, minta ulang
                pw["pesan"] = None
                _save_pending_wa(pw)
                dari = pw["dari"]
                text = f'Oke, pesan ke {dari} dikoreksi. Mau kirim apa?'
                return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

        if _CANCEL_PATTERN.match(msg_stripped):
            _save_pending_wa(None)
            return {"text": "Oke, dibatalkan.", "tts_text": "Oke, dibatalkan.", "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

    if _WA_QUERY_PATTERN.search(message):
        display_text, tts_text = _get_wa_messages()
        return {"text": display_text, "tts_text": tts_text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

    bales_m = _BALES_PATTERN.match(message)
    if bales_m:
        nama = bales_m.group(1).strip()
        pesan = bales_m.group(2).strip()
        chat_id, dari = _find_chat_id(nama)
        if not chat_id:
            text = f"Tidak ketemu kontak dengan nama '{nama}' di pesan WA yang tersimpan."
            return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}
        _save_pending_wa({"chat_id": chat_id, "dari": dari, "pesan": pesan})
        text = f'Siap, mau kirim ke {dari} ya. Pesannya: "{pesan}". Bener?'
        return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

    # "kirim WA ke [nama]: [pesan]" atau "kirim WA ke [nama]" — cari kontak dari seluruh daftar WA
    kirim_m = _KIRIM_PATTERN.match(message) or _KIRIM_NOPESAN_PATTERN.match(message)
    if kirim_m:
        nama = kirim_m.group(1).strip()
        pesan = kirim_m.group(2).strip() if kirim_m.lastindex >= 2 and kirim_m.group(2) else None
        contacts = _search_contacts(nama)
        if contacts is _WA_LOADING:
            text = "Daftar kontak WA sedang dimuat, tunggu sebentar lalu coba lagi."
            return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}
        if not contacts:
            text = f'Tidak ketemu kontak "{nama}" di WA kamu. Coba nama lain?'
            return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}
        if len(contacts) == 1:
            c = contacts[0]
            _save_pending_wa({"chat_id": c["id"], "dari": c["name"], "pesan": pesan})
            if pesan:
                text = f'Ketemu — {c["name"]}. Mau kirim: "{pesan}". Bener?'
            else:
                text = f'Ketemu — {c["name"]}. Mau kirim pesan apa?'
            return {"text": text, "tts_text": text, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}
        # Banyak kontak — minta Wildan pilih
        _save_pending_wa({"chat_id": None, "dari": None, "pesan": pesan, "candidates": contacts})
        lines = [f'Nemu {len(contacts)} kontak dengan nama "{nama}":\n']
        for i, c in enumerate(contacts, 1):
            lines.append(f'{i}. {c["name"]}')
        lines.append('\nYang mana?')
        text = "\n".join(lines)
        tts = f'Nemu {len(contacts)} kontak. ' + ', '.join(f'nomor {i}, {c["name"]}' for i, c in enumerate(contacts, 1)) + '. Yang mana?'
        return {"text": text, "tts_text": tts, "needs_approval": False, "approval_id": None, "run_id": None, "canvas": None}

    from el_solver.config import settings
    from el_solver.core.conversation import get_manager
    from el_solver.core.orchestrator import get_orchestrator
    from el_solver.channels import handler as msg_handler

    user_id = str(settings.telegram_owner_id)

    # Load context
    cm = get_manager()
    ctx = cm.get_context(user_id, "telegram", max_recent=5)

    conv_ctx_str = ""
    # only inject conversation context when the current message looks like a follow-up
    # (mirror behavior in telegram_bot to avoid false-positive mode changes)
    if ctx.recent_turns and _should_include_conversation_context(message):
        conv_ctx_str = "\n".join(
            f"Wildan: {t['user_text']}\nEL SOLVER: {t.get('bot_text', '') or '...'}"
            for t in ctx.recent_turns[-3:]
        )

    # Classify intent
    orch = get_orchestrator(llm_fallback=True)
    intent = orch.classify(message, conversation_context=conv_ctx_str)

    # History untuk conversation handler
    history = None
    if intent.mode.value == "conversation":
        history = [
            (t["user_text"], t["bot_text"] or "")
            for t in ctx.recent_turns
            if t.get("bot_text")
        ] or None

    # Route ke handler
    response = await msg_handler.handle(
        intent,
        channel="telegram",
        user_id=user_id,
        conversation_history=history,
        claude_cli_conv_id=ctx.claude_cli_conv_id,
    )

    # Simpan pending plan kalau perlu approval
    if response.needs_approval and response.approval_request_id and response.pending_plan:
        _pending_plans[response.approval_request_id] = response.pending_plan
        logger.info(f"web: approval pending {response.approval_request_id[:8]}")

    # Persist turn (hanya kalau bukan approval dan ada teks)
    if not response.needs_approval and response.text:
        try:
            new_conv_id = response.metadata.get("claude_cli_conv_id")
            cm.persist_turn(
                user_id=user_id,
                channel="telegram",
                user_text=message,
                bot_text=response.text,
                intent_mode=intent.mode.value,
                run_id=response.metadata.get("run_id"),
                claude_cli_conv_id=new_conv_id or ctx.claude_cli_conv_id,
            )
            cm.summarize_if_threshold(user_id, "telegram")
        except Exception as exc:
            logger.warning(f"web: persist_turn failed (non-critical): {exc}")

    run_id = response.metadata.get("run_id")

    # Build canvas inline kalau ada carousel images
    canvas = None
    from pathlib import Path as _Path
    _static_root = _Path(__file__).parent.parent / "web" / "static"

    # Cek thumbnail preview dulu
    if run_id and canvas is None:
        _thumb_preview_root = _static_root / "thumbnail_preview"
        _thumb_dir = _thumb_preview_root / run_id
        if (_thumb_dir / "thumbnail.jpg").exists():
            _state: dict = {}
            try:
                _sp = _thumb_dir / "state.json"
                if _sp.exists():
                    import json as _json
                    _state = _json.loads(_sp.read_text(encoding="utf-8"))
            except Exception:
                pass
            canvas = {
                "type": "thumbnail",
                "data": {
                    "image": f"/static/thumbnail_preview/{run_id}/thumbnail.jpg",
                    "run_id": run_id,
                    "hook": _state.get("hook_used", ""),
                    "slug": _state.get("slug", ""),
                },
            }

    raw_images: list[str] = response.metadata.get("images", [])
    if raw_images:
        _preview_root = _static_root / "carousel_preview"
        web_images = []
        for img_path in raw_images:
            try:
                rel = _Path(img_path).relative_to(_static_root)
                web_images.append(f"/static/{rel.as_posix()}")
            except ValueError:
                pass
        if web_images:
            has_json = run_id is not None and (_preview_root / run_id / "carousel.json").exists()
            account = response.metadata.get("account", "").lstrip("@")
            canvas = {
                "type": "carousel",
                "data": {
                    "images": web_images,
                    "theme": response.metadata.get("theme", ""),
                    "account": account,
                    "run_id": run_id,
                    "has_json": has_json,
                },
            }

    return {
        "text": response.text,
        "needs_approval": response.needs_approval,
        "approval_id": response.approval_request_id,
        "run_id": run_id,
        "is_decision_approval": response.metadata.get("is_decision_approval", False),
        "canvas": canvas,
    }


async def approve_web(approval_id: str, decision: str) -> dict:
    """Handle approve/reject dari web. Return dict hasil."""
    from el_solver.config import settings
    from el_solver.core import approval as approval_module
    from el_solver.channels import handler as msg_handler

    user_id = str(settings.telegram_owner_id)
    is_approved = decision == "approve"

    try:
        approval_module.decide(approval_id, approved=is_approved, decided_by="wildan-web")
    except FileNotFoundError:
        return {"ok": False, "text": "Request approval tidak ditemukan (mungkin sudah kadaluarsa)."}
    except ValueError as e:
        return {"ok": False, "text": str(e)}

    if not is_approved:
        _pending_plans.pop(approval_id, None)
        return {"ok": True, "text": "Dibatalkan.", "approved": False}

    plan = _pending_plans.get(approval_id)
    if plan is None:
        return {
            "ok": True,
            "text": "Approval tercatat, tapi plan tidak ditemukan di memory (bot mungkin restart). Kirim ulang permintaan.",
            "approved": True,
        }

    try:
        response = await msg_handler.materialize_after_approval(plan, "telegram", user_id)
        _pending_plans.pop(approval_id, None)
        run_id = response.metadata.get("run_id")
        return {"ok": True, "text": response.text, "approved": True, "run_id": run_id}
    except Exception as e:
        logger.exception("web: materialize_after_approval gagal")
        return {"ok": False, "text": f"Gagal: {e}"}
