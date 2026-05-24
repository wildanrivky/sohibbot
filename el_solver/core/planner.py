"""
Planner — generate execution plan dari natural language request.

Input : pesan user (string)
Output: PlanV1 — Pydantic model berisi steps, agents, tools, dependencies.

Flow:
  1. Build structured prompt dari request + context
  2. Panggil Claude CLI, minta output JSON
  3. Parse + validate dengan Pydantic
  4. Return PlanV1 (atau raise PlanError kalau gagal)

PlanV1 sengaja dibuat flat dan simpel — tidak ada DAG kompleks.
Phase 2+ bisa extend ke PlanV2 dengan dependency graph.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from el_solver.llm import call_claude_cli
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


# ── Enums ──────────────────────────────────────────────────────────────────────

class AgentArchetype(str, Enum):
    CONVERSATIONAL = "conversational"   # chat stateful
    SCHEDULED      = "scheduled"        # cron-based
    REACTIVE       = "reactive"         # event/webhook triggered
    PIPELINE       = "pipeline"         # multi-step batch


class StepType(str, Enum):
    LLM_CALL    = "llm_call"       # panggil Claude CLI
    TOOL_CALL   = "tool_call"      # panggil MCP tool / builtin skill
    AGENT_CALL  = "agent_call"     # panggil agent lain
    USER_INPUT  = "user_input"     # butuh input dari user
    CONDITION   = "condition"      # branching


# ── PlanV1 Schema (Pydantic) ──────────────────────────────────────────────────

class PlanStep(BaseModel):
    id: str                              # "step-1", "step-2", dst.
    type: StepType
    description: str                     # human-readable
    tool_or_agent: Optional[str] = None  # nama tool/agent yang dipanggil
    input_from: list[str] = Field(default_factory=list)   # step ids yang jadi input
    output_key: Optional[str] = None     # key untuk simpan output ke context
    agent_assignee: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = False

    @field_validator("id")
    @classmethod
    def id_format(cls, v: str) -> str:
        if not re.match(r"^step-\d+$", v):
            raise ValueError(f"id harus format 'step-N', got: {v!r}")
        return v


class AgentSpec(BaseModel):
    name: str                            # slug: lowercase, dash-separated
    archetype: AgentArchetype
    role_description: str
    tools_required: list[str] = Field(default_factory=list)
    memory_scopes: list[str] = Field(default_factory=list)
    schedule: Optional[str] = None       # cron expression jika archetype=SCHEDULED
    trigger: Optional[str] = None        # event name jika archetype=REACTIVE

    @field_validator("name")
    @classmethod
    def name_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9\-]*$", v):
            raise ValueError(f"name harus slug (lowercase + dash), got: {v!r}")
        return v


class PlanV1(BaseModel):
    version: str = "1"
    request_summary: str                 # ringkasan 1 kalimat dari request user
    mode: str                            # mode dari orchestrator (create_agent, dll)
    agents: list[AgentSpec] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    tools_needed: list[str] = Field(default_factory=list)
    risk_level: str = "L0"              # L0 / L1 / L2 / L3
    clarification_needed: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    raw_llm_response: str = ""          # simpan untuk debug

    @field_validator("risk_level")
    @classmethod
    def valid_risk(cls, v: str) -> str:
        if v not in ("L0", "L1", "L2", "L3"):
            raise ValueError(f"risk_level harus L0/L1/L2/L3, got: {v!r}")
        return v


# ── Errors ────────────────────────────────────────────────────────────────────

class PlanError(Exception):
    """Gagal generate atau parse plan."""


# ── System prompt untuk planner ───────────────────────────────────────────────

_PLANNER_SYSTEM = """\
Kamu adalah AI planner untuk EL SOLVER, sebuah "Agent Creator Agent".

Tugasmu: baca request user, lalu generate execution plan dalam format JSON.

PENTING:
- Jawab HANYA dengan satu blok JSON yang valid. Tidak ada teks di luar JSON.
- Gunakan struktur PlanV1 yang diberikan.
- risk_level: L0=read-only/safe, L1=write lokal, L2=kirim ke pihak ketiga, L3=finansial/hapus data
- Kalau request ambigu dan butuh klarifikasi, set clarification_needed=true dan isi clarification_questions.

Schema JSON yang harus diikuti:
{
  "version": "1",
  "request_summary": "string — ringkasan 1 kalimat",
  "mode": "create_agent | invoke_agent | maintain_agent | conversation",
  "agents": [
    {
      "name": "slug-lowercase-dengan-dash",
      "archetype": "conversational | scheduled | reactive | pipeline",
      "role_description": "string",
      "tools_required": ["string"],
      "memory_scopes": ["global", "agent", "shared"],
      "schedule": "cron string atau null",
      "trigger": "event name atau null"
    }
  ],
  "steps": [
    {
      "id": "step-1",
      "type": "llm_call | tool_call | agent_call | user_input | condition",
      "description": "string",
      "tool_or_agent": "string atau null",
      "input_from": [],
      "output_key": "string atau null"
    }
  ],
  "tools_needed": ["string"],
  "risk_level": "L0",
  "clarification_needed": false,
  "clarification_questions": []
}
"""


# ── JSON extractor ────────────────────────────────────────────────────────────

def _extract_json(text: str) -> str:
    """Ekstrak blok JSON dari response LLM (bisa ada teks sebelum/sesudah)."""
    # Coba cari ```json ... ``` block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # Coba ambil objek JSON pertama yang muncul
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return m.group(0)
    raise PlanError(f"Tidak ada JSON dalam response LLM: {text[:200]!r}")


# ── Public API ────────────────────────────────────────────────────────────────

def generate_plan(
    request: str,
    mode: str = "create_agent",
    context: Optional[str] = None,
    timeout: int = 120,
) -> PlanV1:
    """
    Generate PlanV1 dari natural language request.

    Args:
        request : pesan user asli
        mode    : mode dari orchestrator (default create_agent)
        context : context tambahan (memory snapshot, agent list, dll)
        timeout : timeout Claude CLI dalam detik

    Returns:
        PlanV1 yang sudah divalidasi

    Raises:
        PlanError: kalau LLM gagal atau JSON tidak valid
    """
    prompt = _build_prompt(request, mode, context)
    logger.info(f"planner: generating plan untuk mode={mode!r}, request={request[:60]!r}")

    try:
        raw, duration, *_ = call_claude_cli(prompt, timeout=timeout)
    except Exception as e:
        raise PlanError(f"Claude CLI gagal: {e}") from e

    logger.debug(f"planner: LLM response ({duration:.1f}s): {raw[:200]!r}")

    try:
        json_str = _extract_json(raw)
        data = json.loads(json_str)
    except (json.JSONDecodeError, PlanError) as e:
        raise PlanError(f"Gagal parse JSON dari LLM: {e}\nRaw: {raw[:300]}") from e

    # Inject raw response untuk debug
    data["raw_llm_response"] = raw
    # Override mode dari orchestrator (lebih reliable dari LLM guess)
    data["mode"] = mode

    try:
        plan = PlanV1.model_validate(data)
    except ValidationError as e:
        raise PlanError(f"PlanV1 validation gagal: {e}\nData: {data}") from e

    if mode == "create_agent" and not plan.agents and not plan.clarification_needed:
        logger.warning(f"planner: agents kosong untuk create_agent. Raw LLM: {raw[:300]!r}")
        raise PlanError(
            "Planner tidak menghasilkan definisi agent. "
            "Coba deskripsikan lebih spesifik apa yang agent ini harus kerjakan."
        )

    logger.info(
        f"planner: plan OK — {len(plan.agents)} agent(s), "
        f"{len(plan.steps)} step(s), risk={plan.risk_level}"
    )
    return plan


def _build_prompt(request: str, mode: str, context: Optional[str]) -> str:
    parts = [_PLANNER_SYSTEM, "\n\n---\n\n"]
    if context:
        parts.append(f"Context tambahan:\n{context}\n\n")
    parts.append(f"Mode: {mode}\n")
    parts.append(f"Request user: {request}\n\n")
    parts.append("Generate plan dalam format JSON:")
    return "".join(parts)


# ── Stub untuk testing (tanpa LLM call) ──────────────────────────────────────

def generate_plan_stub(request: str, mode: str = "create_agent") -> PlanV1:
    """
    Return plan minimal tanpa memanggil LLM.
    Dipakai di unit test supaya tidak butuh Claude CLI.
    """
    return PlanV1(
        request_summary=f"[stub] {request[:80]}",
        mode=mode,
        agents=[
            AgentSpec(
                name="stub-agent",
                archetype=AgentArchetype.SCHEDULED,
                role_description="Stub agent untuk testing",
                tools_required=[],
                memory_scopes=["agent"],
            )
        ],
        steps=[
            PlanStep(id="step-1", type=StepType.LLM_CALL, description="Stub step"),
        ],
        tools_needed=[],
        risk_level="L0",
    )
