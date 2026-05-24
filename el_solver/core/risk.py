"""
Risk Assessor — klasifikasi risiko plan/action sebelum dieksekusi.

Level risiko:
  L0 — aman, eksekusi langsung tanpa approval
  L1 — rendah, eksekusi dengan log saja
  L2 — sedang, butuh approval Wildan via Telegram
  L3 — tinggi, STOP dan eskalasi — tidak boleh otomatis

Modul ini murni sinkron & deterministik (tidak butuh LLM call).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from el_solver.core.planner import AgentSpec, PlanV1
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


# ── Risk level ─────────────────────────────────────────────────────────────────

RISK_LEVELS = ("L0", "L1", "L2", "L3")


@dataclass
class RiskResult:
    level: str                          # "L0" | "L1" | "L2" | "L3"
    reasons: list[str] = field(default_factory=list)
    requires_approval: bool = False
    blocked: bool = False               # True → jangan eksekusi sama sekali
    override_level: Optional[str] = None  # kalau plan sudah set level lebih tinggi

    def __post_init__(self) -> None:
        if self.level not in RISK_LEVELS:
            raise ValueError(f"Level tidak valid: {self.level!r}. Harus salah satu dari {RISK_LEVELS}")
        self.requires_approval = self.level in ("L2", "L3")
        self.blocked = self.level == "L3"


# ── Sinyal risiko ─────────────────────────────────────────────────────────────

# Tool yang langsung naikkan ke L2
_HIGH_RISK_TOOLS = frozenset({
    "shell.execute",
    "bash",
    "exec",
    "subprocess",
    "system",
    "os.system",
    "eval",
    "rm",
    "delete_file",
    "drop_table",
    "db.execute",
})

# Tool yang L1 (perlu log tapi tidak perlu approval)
_MEDIUM_RISK_TOOLS = frozenset({
    "web_search",
    "http_request",
    "send_email",
    "send_message",
    "telegram_send",
    "write_file",
    "create_file",
    "db.read",
})

# Kata kunci dalam request/role yang menandakan risiko tinggi
_HIGH_RISK_KEYWORDS = re.compile(
    r"\b(hapus|delete|drop|truncate|rm -rf|format|wipe|destroy|"
    r"password|secret|token|credential|akses root|sudo|admin|"
    r"production|prod db|database prod|live server)\b",
    re.IGNORECASE,
)

# Kata kunci dalam request/role yang menandakan risiko sedang
_MEDIUM_RISK_KEYWORDS = re.compile(
    r"\b(kirim|send|email|telegram|publish|post|upload|"
    r"update data|modif|perbarui|simpan ke|write to|tulis ke)\b",
    re.IGNORECASE,
)


# ── Core assessment logic ──────────────────────────────────────────────────────

def _assess_tools(spec: AgentSpec) -> tuple[str, list[str]]:
    """Return (max_level, reasons) berdasarkan tools yang dipakai agent."""
    level = "L0"
    reasons: list[str] = []

    for tool in spec.tools_required:
        tool_lower = tool.lower()
        if tool_lower in _HIGH_RISK_TOOLS:
            level = "L2"
            reasons.append(f"Tool berisiko tinggi: `{tool}`")
        elif tool_lower in _MEDIUM_RISK_TOOLS and level == "L0":
            level = "L1"
            reasons.append(f"Tool membutuhkan log: `{tool}`")

    return level, reasons


def _assess_keywords(text: str) -> tuple[str, list[str]]:
    """Return (max_level, reasons) berdasarkan kata kunci dalam teks."""
    if not text:
        return "L0", []

    level = "L0"
    reasons: list[str] = []

    if _HIGH_RISK_KEYWORDS.search(text):
        matches = _HIGH_RISK_KEYWORDS.findall(text)
        level = "L2"
        reasons.append(f"Kata kunci berisiko: {', '.join(set(matches))}")
    elif _MEDIUM_RISK_KEYWORDS.search(text):
        matches = _MEDIUM_RISK_KEYWORDS.findall(text)
        level = "L1"
        reasons.append(f"Kata kunci sedang: {', '.join(set(matches))}")

    return level, reasons


def _max_level(*levels: str) -> str:
    """Ambil level tertinggi dari beberapa level."""
    return max(levels, key=lambda l: RISK_LEVELS.index(l))


# ── Public API ─────────────────────────────────────────────────────────────────

def assess_spec(spec: AgentSpec) -> RiskResult:
    """
    Nilai risiko satu AgentSpec.

    Menggabungkan sinyal dari:
    - tools_required
    - role_description
    - schedule/trigger presence
    """
    reasons: list[str] = []

    tool_level, tool_reasons = _assess_tools(spec)
    reasons.extend(tool_reasons)

    kw_level, kw_reasons = _assess_keywords(spec.role_description)
    reasons.extend(kw_reasons)

    # Agent scheduled/reactive punya akses lebih otomatis → minimal L1
    auto_level = "L0"
    if spec.archetype.value in ("scheduled", "reactive"):
        auto_level = "L1"
        reasons.append(f"Archetype {spec.archetype.value} berjalan otomatis → min L1")

    level = _max_level(tool_level, kw_level, auto_level)

    result = RiskResult(level=level, reasons=reasons)
    logger.debug(f"risk.assess_spec '{spec.name}': {level} ({len(reasons)} reasons)")
    return result


def assess_plan(plan: PlanV1) -> RiskResult:
    """
    Nilai risiko keseluruhan PlanV1.

    Ambil level tertinggi dari:
    1. plan.risk_level (sudah ada dari planner)
    2. Setiap AgentSpec di plan.agents
    3. Kata kunci di plan.request_summary
    """
    reasons: list[str] = []

    # Level dari planner
    planner_level = plan.risk_level or "L0"
    if planner_level != "L0":
        reasons.append(f"Planner sudah menandai {planner_level}")

    # Level dari setiap agent
    agent_max = "L0"
    for spec in plan.agents:
        r = assess_spec(spec)
        if RISK_LEVELS.index(r.level) > RISK_LEVELS.index(agent_max):
            agent_max = r.level
            reasons.extend([f"[{spec.name}] {reason}" for reason in r.reasons])

    # Level dari request summary
    kw_level, kw_reasons = _assess_keywords(plan.request_summary)
    reasons.extend(kw_reasons)

    final = _max_level(planner_level, agent_max, kw_level)
    result = RiskResult(level=final, reasons=reasons)
    logger.info(f"risk.assess_plan: {final} ({len(reasons)} reasons)")
    return result


def assess_action(action: str, tools: list[str] | None = None) -> RiskResult:
    """
    Nilai risiko satu action string (untuk INVOKE/MAINTAIN mode).

    Args:
        action : deskripsi aksi yang akan dijalankan
        tools  : list tool yang akan dipakai (opsional)
    """
    reasons: list[str] = []

    kw_level, kw_reasons = _assess_keywords(action)
    reasons.extend(kw_reasons)

    tool_level = "L0"
    for tool in (tools or []):
        tool_lower = tool.lower()
        if tool_lower in _HIGH_RISK_TOOLS:
            tool_level = _max_level(tool_level, "L2")
            reasons.append(f"Tool berisiko tinggi: `{tool}`")
        elif tool_lower in _MEDIUM_RISK_TOOLS:
            tool_level = _max_level(tool_level, "L1")
            reasons.append(f"Tool membutuhkan log: `{tool}`")

    final = _max_level(kw_level, tool_level)
    result = RiskResult(level=final, reasons=reasons)
    logger.debug(f"risk.assess_action: {final}")
    return result


def gate(result: RiskResult, auto_approve_up_to: str = "L1") -> bool:
    """
    Cek apakah eksekusi boleh dilanjutkan tanpa approval manual.

    Args:
        result           : hasil dari assess_*
        auto_approve_up_to : level tertinggi yang diizinkan otomatis (default L1)

    Returns:
        True  → boleh lanjut otomatis
        False → butuh approval atau blocked
    """
    if result.blocked:  # L3 selalu blocked, tidak boleh dioverride
        return False
    max_auto = RISK_LEVELS.index(auto_approve_up_to)
    current = RISK_LEVELS.index(result.level)
    return current <= max_auto
