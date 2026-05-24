#!/usr/bin/env python3
"""
SohibBot — Telegram Bot.

Jalankan:
  python scripts/claude_telegram.py

Setup:
  1. Buat bot di @BotFather, dapat token
  2. Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_OWNER_ID di .env
  3. Jalankan python scripts/claude_telegram.py
"""

import asyncio
import html
import json
import logging
import os
import re
import signal
import uuid
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN = os.getenv("CLAUDE_TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))
CLAUDE_CLI = os.getenv("CLAUDE_CLI_PATH", "claude")
USER_NAME = os.getenv("USER_NAME", "kamu")
BOT_NAME = os.getenv("BOT_NAME", "SohibBot")
BOT_SLUG = os.getenv("BOT_SLUG", "sohibbot")
SERVICE_LABEL = os.getenv("SERVICE_LABEL", "com.user.sohibbot")
BOT_ERR_LOG = os.getenv("BOT_ERR_LOG", f"/tmp/{BOT_SLUG}.error.log")
WORKDIR = str(Path(__file__).parent.parent)
HISTORY_FILE = Path(WORKDIR) / "data" / "conversations" / "telegram_history.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MAX_MSG_LEN = 4000
MAX_HISTORY = 20


def load_history() -> dict:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return {}


def save_history(history: dict) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


_AGENT_GUARD = (
    "[INSTRUKSI SISTEM: Jangan jalankan sub-agent atau tools berat kecuali user secara EKSPLISIT "
    "menyebutkan nama agent atau task-nya. Kalau tidak yakin, jawab percakapan biasa.]\n\n"
)


def build_prompt(chat_history: list, new_message: str) -> str:
    if not chat_history:
        return _AGENT_GUARD + new_message
    lines = ["Riwayat percakapan sebelumnya:"]
    for entry in chat_history:
        prefix = USER_NAME if entry["role"] == "user" else BOT_NAME
        lines.append(f"{prefix}: {entry['content']}")
    lines.append("")
    lines.append(f"Pesan baru dari {USER_NAME}: {new_message}")
    return _AGENT_GUARD + "\n".join(lines)


def split_message(text: str) -> list[str]:
    if len(text) <= MAX_MSG_LEN:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= MAX_MSG_LEN:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, MAX_MSG_LEN)
        if split_at == -1:
            split_at = MAX_MSG_LEN
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


async def _keep_typing(chat_id: int, context: ContextTypes.DEFAULT_TYPE, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=4.0)
        except asyncio.TimeoutError:
            pass
        except Exception:
            break


HALU_PATTERN = re.compile(
    r"^(?:kamu\s+)?halu(?:\s+lagi)?[!.]*$|"
    r"^(?:ada\s+)?bug[!.]*$|"
    r"^(?:kamu\s+)?error[!.]*$",
    re.IGNORECASE,
)
_background_tasks: set = set()


async def trigger_self_fix(send_fn, context_msg: str = "") -> None:
    import platform
    if platform.system() == "Darwin":
        restart_cmd = f"launchctl stop {SERVICE_LABEL} && sleep 2 && launchctl start {SERVICE_LABEL}"
    elif platform.system() == "Linux":
        restart_cmd = f"systemctl --user restart {BOT_SLUG}.service"
    else:
        restart_cmd = f"schtasks /end /tn {BOT_SLUG} && schtasks /run /tn {BOT_SLUG}"

    prompt = (
        f"Bot Telegram {BOT_NAME} mengalami bug.\n"
        f"Konteks: \"{context_msg}\"\n"
        f"1. Baca 50 baris terakhir error log: {BOT_ERR_LOG}\n"
        f"2. Baca kode bot: {Path(__file__).resolve()}\n"
        f"3. Identifikasi bug, perbaiki langsung di file tersebut\n"
        f"4. Restart bot: {restart_cmd}\n"
        f"5. Tulis summary singkat (max 3 kalimat)"
    )
    claude_bin = CLAUDE_CLI
    bot_script = str(Path(__file__).resolve())
    proc = await asyncio.create_subprocess_exec(
        claude_bin, "--dangerously-skip-permissions", "-p", prompt,
        cwd=str(Path(bot_script).parent.parent),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        summary = stdout.decode("utf-8", errors="replace").strip()
        reply = f"Selesai diperbaiki:\n\n{summary[:1000]}" if summary else "Bot sudah direstart. Coba lagi."
        await send_fn(reply)
    except asyncio.TimeoutError:
        proc.kill()
        await send_fn("Timeout. Coba perbaiki manual.")


def _build_menu_text() -> str:
    return (
        f"Halo {USER_NAME}! Mau ngerjain apa?\n\n"
        "<b>Perintah</b>\n"
        "/learn [topik] — belajar dari web\n"
        "/note [teks] — simpan catatan cepat\n"
        "/memory — lihat semua catatan\n"
        "/reset — hapus riwayat percakapan\n"
        "/restart — restart bot\n\n"
        "Atau ketik pesan biasa untuk chat langsung ke Claude."
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text(_build_menu_text(), parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text(_build_menu_text(), parse_mode="HTML")


async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    topic = " ".join(context.args).strip() if context.args else ""
    if not topic:
        await update.message.reply_text("Topik apa?\nContoh: <code>/learn marketing digital</code>", parse_mode="HTML")
        return
    placeholder = await update.message.reply_text(f"Sedang belajar tentang '{topic}'...")
    try:
        from el_solver.tools.web_learner import search_and_learn
        result = await asyncio.to_thread(search_and_learn, topic)
    except Exception as exc:
        log.exception("cmd_learn error")
        result = f"Gagal belajar dari web: {exc}"
    await placeholder.edit_text(result)


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    try:
        from el_solver import memory
        entries = memory.list_all(exclude_always=False)
    except Exception as exc:
        await update.message.reply_text(f"Gagal baca memory: {exc}")
        return
    if not entries:
        await update.message.reply_text("Memory kosong.")
        return
    lines = [
        f"<code>{html.escape(e.relative_path)}</code> — {html.escape(e.description or '(no desc)')}"
        for e in entries
    ]
    text = f"<b>Memory ({len(entries)} entries):</b>\n" + "\n".join(lines)
    for chunk in split_message(text):
        await update.message.reply_text(chunk, parse_mode="HTML")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    chat_id = str(update.effective_chat.id)
    history = load_history()
    history.pop(chat_id, None)
    save_history(history)
    await update.message.reply_text("Konteks percakapan direset.")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    await update.message.reply_text("Restarting bot... tunggu sebentar.")
    os.kill(os.getpid(), signal.SIGTERM)


async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text("Tulis catatannya setelah /note\nContoh: <code>/note ide penting</code>", parse_mode="HTML")
        return
    update.message.text = f"catat ini ke memory: {text}"
    await handle_message(update, context)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    if update.effective_user.id != OWNER_ID:
        await query.answer("Akses ditolak.")
        return
    await query.answer()
    if ":" not in query.data:
        return
    action, request_id = query.data.split(":", 1)
    is_approved = action in ("approve", "dc_approve")
    new_status = "approved" if is_approved else "rejected"

    try:
        from el_solver.utils.db import get_connection
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE approvals SET status=?, decided_at=CURRENT_TIMESTAMP WHERE task_id=? OR request_id=?",
                (new_status, request_id, request_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"callback DB error: {e}")

    if is_approved:
        await query.edit_message_text(f"Approved — {request_id[:12]}")
    else:
        await query.edit_message_text(f"Rejected — {request_id[:12]}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != OWNER_ID:
        return
    user_message = (update.message.text or "").strip()
    if not user_message:
        return

    chat_id = str(update.effective_chat.id)
    tg_chat_id = update.effective_chat.id

    if re.match(r'^halo[.!]?\s*$', user_message, re.IGNORECASE):
        await update.message.reply_text(_build_menu_text(), parse_mode="HTML")
        return

    if HALU_PATTERN.match(user_message):
        await update.message.reply_text("Memanggil Claude Code untuk baca log dan perbaiki (maks 5 menit)...")
        task = asyncio.create_task(trigger_self_fix(
            lambda msg: update.message.reply_text(msg),
            context_msg=user_message,
        ))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return

    placeholder = await update.message.reply_text("Memproses...")
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(tg_chat_id, context, stop_typing))

    history = load_history()
    chat_history = history.get(chat_id, [])
    prompt = build_prompt(chat_history, user_message)

    PROGRESS_INTERVAL = 120
    TIMEOUT = 3600

    async def _send_progress(start_time: float) -> None:
        messages = [
            "masih proses... sabar ya",
            "Claude masih kerja, belum selesai",
            "hampir selesai (mungkin)",
        ]
        idx = 0
        while True:
            await asyncio.sleep(PROGRESS_INTERVAL)
            elapsed = int((asyncio.get_running_loop().time() - start_time) / 60)
            try:
                await update.message.reply_text(f"{messages[idx % len(messages)]} ({elapsed} menit)")
            except Exception:
                pass
            idx += 1

    try:
        claude_bin = CLAUDE_CLI
        proc = await asyncio.create_subprocess_exec(
            claude_bin, "--print", "--dangerously-skip-permissions", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=WORKDIR,
        )
        start_time = asyncio.get_running_loop().time()
        progress_task = asyncio.create_task(_send_progress(start_time))
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        finally:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
        reply = stdout.decode().strip() or stderr.decode().strip() or "(tidak ada output)"
    except asyncio.TimeoutError:
        reply = "Timeout 1 jam. Coba pecah task jadi beberapa prompt."
    except Exception as e:
        reply = f"Error: {e}"
    finally:
        stop_typing.set()
        await typing_task

    chat_history.append({"role": "user", "content": user_message})
    chat_history.append({"role": "assistant", "content": reply})
    while len(chat_history) > MAX_HISTORY * 2:
        chat_history.pop(0)
        chat_history.pop(0)
    history[chat_id] = chat_history
    save_history(history)

    chunks = split_message(reply)
    await placeholder.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await update.message.reply_text(chunk)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram.error import Conflict, NetworkError, TimedOut
    if isinstance(context.error, Conflict):
        log.warning("Conflict: ada instance bot lain. Diabaikan.")
        return
    if isinstance(context.error, (NetworkError, TimedOut)):
        log.warning("Network error (retry otomatis): %s", context.error)
        return
    log.error("Error: %s", context.error, exc_info=context.error)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN tidak ada di .env — buat bot di @BotFather")
    if not OWNER_ID:
        raise SystemExit("TELEGRAM_OWNER_ID tidak ada di .env — cek via @userinfobot")

    try:
        from el_solver.utils.db import migrate
        migrate()
    except Exception as e:
        log.warning(f"DB migrate skip: {e}")

    log.info(f"{BOT_NAME} dimulai. Owner: {OWNER_ID}, workdir: {WORKDIR}")
    owner_filter = filters.User(user_id=OWNER_ID)

    from telegram import BotCommand
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   cmd_start,   filters=owner_filter))
    app.add_handler(CommandHandler("help",    cmd_help,    filters=owner_filter))
    app.add_handler(CommandHandler("learn",   cmd_learn,   filters=owner_filter))
    app.add_handler(CommandHandler("note",    cmd_note,    filters=owner_filter))
    app.add_handler(CommandHandler("memory",  cmd_memory,  filters=owner_filter))
    app.add_handler(CommandHandler("reset",   cmd_reset,   filters=owner_filter))
    app.add_handler(CommandHandler("restart", cmd_restart, filters=owner_filter))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & owner_filter, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_error_handler(error_handler)

    async def _set_commands(_app):
        await _app.bot.set_my_commands([
            BotCommand("learn",   "Belajar dari web tentang topik"),
            BotCommand("note",    "Simpan catatan cepat"),
            BotCommand("memory",  "Lihat semua catatan"),
            BotCommand("reset",   "Hapus riwayat percakapan"),
            BotCommand("restart", "Restart bot"),
            BotCommand("help",    "Tampilkan menu"),
        ])
    app.post_init = _set_commands

    log.info("Bot running. Ctrl+C untuk stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
