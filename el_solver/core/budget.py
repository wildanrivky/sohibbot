"""
Budget Enforcer — Round 1, subscription mode.

Mode: subscription via CLI subprocess.
Cost: estimated (token × reference pricing), BUKAN actual billing.
Overflow action: alert only — TIDAK hard-kill process.

7 layers (blueprint section 7.6):
  1. Pre-execution cost estimation
  2. Per-task cap
  3. Per-agent daily cap
  4. Global daily cap
  5. Monthly cap (rolling 30 hari)
  6. Max iteration cap (3 retry)
  7. Wall-clock timeout (5 menit)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

# Reference pricing Claude Sonnet (estimated — subscription mode)
_INPUT_PRICE_PER_TOKEN = 3.0 / 1_000_000
_OUTPUT_PRICE_PER_TOKEN = 15.0 / 1_000_000

# Defaults kalau agent tidak punya config khusus
_DEFAULT_PER_TASK_USD = 0.20
_DEFAULT_AGENT_DAILY_USD = 2.00
_DEFAULT_GLOBAL_DAILY_USD = 33.00
_DEFAULT_MONTHLY_USD = 1000.00
_WARNING_THRESHOLD = 0.80
_MAX_RETRIES = 3
_WALL_CLOCK_TIMEOUT_S = 300


@dataclass
class BudgetResult:
    ok: bool
    alerts: list[str]
    layer: str
    message: str

    @classmethod
    def proceed(cls, alerts: list[str] | None = None) -> "BudgetResult":
        return cls(ok=True, alerts=alerts or [], layer="none", message="ok")

    @classmethod
    def alert(cls, layer: str, message: str, alerts: list[str] | None = None) -> "BudgetResult":
        return cls(ok=True, alerts=alerts or [message], layer=layer, message=message)


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimasi cost dari token count. Mode subscription = bukan actual billing."""
    return input_tokens * _INPUT_PRICE_PER_TOKEN + output_tokens * _OUTPUT_PRICE_PER_TOKEN


def estimate_tokens_from_message(message: str, output_multiplier: float = 2.0) -> tuple[int, int]:
    """Rough estimate: 1 token ≈ 4 chars. Output ≈ 2× input."""
    input_tokens = max(len(message) // 4, 10)
    output_tokens = int(input_tokens * output_multiplier)
    return input_tokens, output_tokens


def _get_agent_budget_config(agent_name: str) -> dict:
    """Baca config budget agent dari DB settings atau fallback ke default."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT token_limit_daily, token_spent_today FROM agents_registry WHERE name=?",
            (agent_name,),
        ).fetchone()
        if not row:
            return {
                "per_task_usd": _DEFAULT_PER_TASK_USD,
                "daily_usd": _DEFAULT_AGENT_DAILY_USD,
                "token_limit_daily": 50000,
                "token_spent_today": 0,
            }
        token_limit = row["token_limit_daily"] or 50000
        token_spent = row["token_spent_today"] or 0
        # Convert token limit ke estimasi USD
        daily_usd = estimate_cost(token_limit, token_limit // 2)
        spent_usd = estimate_cost(token_spent, token_spent // 2)
        return {
            "per_task_usd": daily_usd / 20,  # per-task = daily/20 by default
            "daily_usd": daily_usd,
            "token_limit_daily": token_limit,
            "token_spent_today": token_spent,
            "daily_usd_spent": spent_usd,
            "daily_usd_remaining": max(daily_usd - spent_usd, 0.0),
        }
    except Exception:
        return {
            "per_task_usd": _DEFAULT_PER_TASK_USD,
            "daily_usd": _DEFAULT_AGENT_DAILY_USD,
            "token_limit_daily": 50000,
            "token_spent_today": 0,
        }
    finally:
        conn.close()


def _get_global_cost(days: int = 1) -> float:
    """Estimasi total cost global untuk N hari terakhir dari runs table."""
    conn = get_connection()
    try:
        row = conn.execute(
            f"""SELECT COALESCE(SUM(cost_usd), 0) as total
                FROM runs
                WHERE started_at > datetime('now', '-{days} days')""",
        ).fetchone()
        return float(row["total"]) if row else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def _emit_budget_alert(agent_name: str, alert_type: str, message: str) -> None:
    """Simpan alert ke budget_alerts table. Non-blocking."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO budget_alerts (id, agent_name, type, message) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), agent_name, alert_type, message),
        )
        conn.commit()
    except Exception as e:
        logger.debug(f"emit_budget_alert failed (non-critical): {e}")
    finally:
        conn.close()


def enforce(agent_name: str, input_tokens: int, output_tokens: int) -> BudgetResult:
    """
    Jalankan semua 7 layer budget check untuk satu task.
    Mode subscription: TIDAK reject, hanya alert (kecuali budget benar-benar habis).

    Returns BudgetResult. Selalu ok=True di subscription mode — overflow = alert.
    """
    alerts: list[str] = []
    estimated = estimate_cost(input_tokens, output_tokens)
    config = _get_agent_budget_config(agent_name)

    # Layer 1: Pre-execution cost estimation (log saja)
    logger.debug(f"budget.L1: agent={agent_name} estimated_cost=${estimated:.5f}")

    # Layer 2: Per-task cap
    per_task_limit = config.get("per_task_usd", _DEFAULT_PER_TASK_USD)
    if estimated > per_task_limit:
        msg = f"[L2] Task estimate ${estimated:.4f} melebihi per-task cap ${per_task_limit:.4f} (subscription mode: lanjut)"
        logger.warning(f"budget: {msg}")
        alerts.append(msg)
        _emit_budget_alert(agent_name, "per_task_exceeded", msg)

    # Layer 3: Per-agent daily cap
    daily_usd = config.get("daily_usd", _DEFAULT_AGENT_DAILY_USD)
    daily_spent = config.get("daily_usd_spent", 0.0)
    daily_remaining = config.get("daily_usd_remaining", daily_usd)
    if daily_spent >= daily_usd:
        msg = f"[L3] Agent '{agent_name}' daily cap habis: ${daily_spent:.4f}/${daily_usd:.4f} (subscription mode: alert)"
        logger.warning(f"budget: {msg}")
        alerts.append(msg)
        _emit_budget_alert(agent_name, "agent_daily_exhausted", msg)
    elif daily_spent / daily_usd >= _WARNING_THRESHOLD:
        pct = daily_spent / daily_usd * 100
        msg = f"[L3] Agent '{agent_name}' mendekati daily cap: {pct:.1f}% terpakai"
        alerts.append(msg)
        _emit_budget_alert(agent_name, "warning_80", msg)

    # Layer 4: Global daily cap
    global_today = _get_global_cost(days=1)
    if global_today >= _DEFAULT_GLOBAL_DAILY_USD:
        msg = f"[L4] Global daily cap tercapai: ${global_today:.2f}/${_DEFAULT_GLOBAL_DAILY_USD:.2f}"
        alerts.append(msg)
        _emit_budget_alert(agent_name, "global_daily_exceeded", msg)
    elif global_today >= _DEFAULT_GLOBAL_DAILY_USD * _WARNING_THRESHOLD:
        pct = global_today / _DEFAULT_GLOBAL_DAILY_USD * 100
        msg = f"[L4] Budget global {pct:.1f}% terpakai hari ini"
        alerts.append(msg)

    # Layer 5: Monthly cap (rolling 30 hari)
    global_monthly = _get_global_cost(days=30)
    if global_monthly >= _DEFAULT_MONTHLY_USD:
        msg = f"[L5] Monthly cap tercapai: ${global_monthly:.2f}/${_DEFAULT_MONTHLY_USD:.2f}"
        alerts.append(msg)
        _emit_budget_alert(agent_name, "monthly_exceeded", msg)
    elif global_monthly >= _DEFAULT_MONTHLY_USD * _WARNING_THRESHOLD:
        pct = global_monthly / _DEFAULT_MONTHLY_USD * 100
        msg = f"[L5] Budget bulanan {pct:.1f}% terpakai"
        alerts.append(msg)

    # Layer 6 + 7 ditangani di execution level (retry counter + timeout)
    # Di sini cukup return BudgetResult.proceed() dengan alerts

    if alerts:
        return BudgetResult.alert("multi", alerts[0], alerts)
    return BudgetResult.proceed()


def get_agent_daily_remaining(agent_name: str) -> float:
    """Return estimasi USD remaining untuk agent hari ini."""
    config = _get_agent_budget_config(agent_name)
    return config.get("daily_usd_remaining", config.get("daily_usd", _DEFAULT_AGENT_DAILY_USD))


# ── Per-message LLM Budget Cap ─────────────────────────────────────────────────
# Lindungi rate-limit: max 4 Claude CLI call per Telegram message (default).
# Counter dikelola di llm.py untuk avoid circular import.

def get_max_llm_calls_per_message() -> int:
    """Baca config max_llm_calls_per_message dari DB settings, default 4."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='max_llm_calls_per_message' LIMIT 1"
        ).fetchone()
        if row:
            return int(row["value"])
    except Exception:
        pass
    finally:
        conn.close()
    return 4


def record_llm_call_count(run_id: str, count: int) -> None:
    """Update runs.llm_call_count untuk run tertentu (best-effort)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE runs SET llm_call_count=? WHERE id=?",
            (count, run_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
