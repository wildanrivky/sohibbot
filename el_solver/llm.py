"""Subprocess wrapper untuk claude CLI."""
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from el_solver.config import settings
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).parent.parent

# Per-message LLM call counter — diset oleh budget.PerMessageBudget, reset per run
_msg_llm_counter: dict[str, int] = {}  # run_id → call count
_msg_llm_max: int = 4  # default cap per pesan


def set_message_llm_budget(run_id: str, max_calls: int = 4) -> None:
    """Inisialisasi per-message LLM call counter untuk satu run."""
    _msg_llm_counter[run_id] = 0
    global _msg_llm_max
    _msg_llm_max = max_calls


def consume_message_llm_budget(run_id: str | None) -> None:
    """Increment counter. Raise BudgetExceeded kalau cap tercapai."""
    if run_id is None:
        return
    count = _msg_llm_counter.get(run_id, 0) + 1
    _msg_llm_counter[run_id] = count
    if count > _msg_llm_max:
        from el_solver.agent import BudgetExceeded
        raise BudgetExceeded(
            f"Per-message LLM cap reached ({count}/{_msg_llm_max}). run_id={run_id[:8]}"
        )


def clear_message_llm_budget(run_id: str) -> int:
    """Return final count dan hapus dari counter."""
    return _msg_llm_counter.pop(run_id, 0)


def call_claude_cli(
    message: str,
    model: str | None = None,
    conversation_id: str | None = None,
    timeout: int = 600,
    run_id: str | None = None,
) -> tuple[str, float, str | None]:
    """Panggil claude CLI dengan -p (non-interactive).

    Return: (response_text, duration_seconds, session_id).
    session_id adalah Claude CLI conversation ID untuk --resume di call berikutnya.
    Gunakan `text, duration, *_ = call_claude_cli(...)` kalau tidak perlu session_id.
    """
    # Per-message LLM budget check (non-blocking kalau run_id tidak diset)
    if run_id:
        try:
            consume_message_llm_budget(run_id)
        except Exception as exc:
            logger.warning(f"llm: budget exceeded: {exc}")
            raise

    effective_model = model or settings.claude_model_default
    cmd = [
        settings.claude_cli_path, "-p", message,
        "--dangerously-skip-permissions",
        "--output-format", "json",
    ]
    if effective_model:
        cmd.extend(["--model", effective_model])
    if conversation_id:
        cmd.extend(["--resume", conversation_id])

    # Strip ANTHROPIC_API_KEY — kalau ada di env (dari .env carousel bot atau lainnya),
    # Claude CLI akan coba pakai itu dan gagal "Invalid API key". Pakai sesi Pro saja.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    logger.debug(f"claude CLI cmd: {' '.join(cmd[:3])} ... (model={effective_model})")
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude CLI timeout setelah {timeout}s")

    duration = time.monotonic() - start

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI gagal (exit {result.returncode}): {result.stderr.strip()}"
        )

    raw_stdout = result.stdout.strip()

    # Parse JSON output dari --output-format json
    session_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    response = raw_stdout  # fallback
    try:
        parsed = json.loads(raw_stdout)
        response = parsed.get("result", raw_stdout)
        session_id = parsed.get("session_id")
        usage = parsed.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
    except (json.JSONDecodeError, AttributeError):
        # Claude CLI mungkin return plain text kalau --output-format json tidak didukung
        response = raw_stdout

    _log_usage(message, response, duration, input_tokens, output_tokens)
    return response, duration, session_id


def _log_usage(
    prompt: str,
    response: str,
    duration: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    try:
        settings.data_path.mkdir(parents=True, exist_ok=True)
        # Fallback heuristic kalau token tidak tersedia dari JSON
        if not input_tokens:
            input_tokens = max(len(prompt) // 4, 1)
        if not output_tokens:
            output_tokens = max(len(response) // 4, 1)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "duration_s": round(duration, 2),
            "prompt_chars": len(prompt),
            "response_chars": len(response),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        with open(settings.usage_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
