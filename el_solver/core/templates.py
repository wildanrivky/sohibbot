"""
Template Renderer — compose agent CLAUDE.md dari Jinja2 templates + capability packs.

Hierarki prompt yang dihasilkan:
1. Base (agents/_templates/_base/CLAUDE.md.j2)
2. Archetype fragment (agents/_templates/<archetype>/archetype.j2)
3. Capability packs (agents/_templates/_capabilities/<cap>/prompt.j2)

Usage:
    renderer = TemplateRenderer()
    claude_md = renderer.render_claude_md(spec, plan)
    manifest_yaml = renderer.render_manifest_yaml(spec, plan)
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from el_solver.config import PROJECT_ROOT
from el_solver.core.planner import AgentArchetype, AgentSpec, PlanV1
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

TEMPLATES_DIR = PROJECT_ROOT / "agents" / "_templates"

# Mapping dari tool name / keyword → capability pack name
_TOOL_TO_CAPABILITY: dict[str, str] = {
    "web_search": "web_research",
    "web_fetch": "web_research",
    "search": "web_research",
    "write_file": "content_writing",
    "text_editor": "content_writing",
    "pages_write": "content_writing",
    "instagram": "instagram",
    "instagram_dm": "instagram",
    "instagram_post": "instagram",
    "repo_read": "code_generation",
    "repo_write": "code_generation",
    "shell": "code_generation",
    "csv_read": "data_analysis",
    "plot": "data_analysis",
    "database": "data_analysis",
    "databases": "data_analysis",
}

# Semua capability pack yang tersedia
_AVAILABLE_CAPABILITIES = {
    "web_research",
    "content_writing",
    "instagram",
    "code_generation",
    "data_analysis",
}


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class CapabilityPack:
    name: str
    content: str


# ── Template Renderer ─────────────────────────────────────────────────────────

class TemplateRenderer:
    """
    Render agent CLAUDE.md dan manifest.yaml dari Jinja2 templates + capability packs.
    Kalau template tidak ada, fallback ke string interpolation sederhana.
    """

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._dir = templates_dir or TEMPLATES_DIR
        self._jinja_env: Environment | None = self._init_jinja()

    def _init_jinja(self) -> Environment | None:
        if not self._dir.exists():
            logger.warning(f"templates: dir tidak ada: {self._dir}")
            return None
        try:
            return Environment(
                loader=FileSystemLoader(str(self._dir)),
                undefined=StrictUndefined,
                keep_trailing_newline=True,
                trim_blocks=True,
                lstrip_blocks=True,
            )
        except Exception as e:
            logger.warning(f"templates: gagal init Jinja2: {e}")
            return None

    # ── Capability packs ──────────────────────────────────────────────────────

    def load_capability_pack(self, name: str) -> CapabilityPack | None:
        """Load satu capability pack dari _capabilities/<name>/prompt.j2."""
        cap_path = self._dir / "_capabilities" / name / "prompt.j2"
        if not cap_path.exists():
            return None
        return CapabilityPack(name=name, content=cap_path.read_text(encoding="utf-8").strip())

    def load_capability_packs(self, names: list[str]) -> list[CapabilityPack]:
        """Load multiple capability packs. Missing packs di-skip dengan warning."""
        result: list[CapabilityPack] = []
        for name in names:
            pack = self.load_capability_pack(name)
            if pack:
                result.append(pack)
            else:
                logger.warning(f"templates: capability pack '{name}' tidak ditemukan")
        return result

    def load_archetype_fragment(self, archetype: AgentArchetype) -> str | None:
        """Load archetype fragment dari _templates/<archetype>/archetype.j2."""
        frag_path = self._dir / archetype.value / "archetype.j2"
        if not frag_path.exists():
            return None
        return frag_path.read_text(encoding="utf-8").strip()

    # ── Tool → capability inference ───────────────────────────────────────────

    @staticmethod
    def infer_capabilities(tools_required: list[str]) -> list[str]:
        """
        Infer capability pack names dari list tool names.
        Dedup dan hanya return capability yang tersedia.
        """
        caps: set[str] = set()
        for tool in tools_required:
            # Match exact
            if tool in _TOOL_TO_CAPABILITY:
                caps.add(_TOOL_TO_CAPABILITY[tool])
                continue
            # Match by substring (e.g. "instagram_dm" matches "instagram")
            for keyword, cap in _TOOL_TO_CAPABILITY.items():
                if keyword in tool.lower() or tool.lower() in keyword:
                    caps.add(cap)
                    break
        return sorted(c for c in caps if c in _AVAILABLE_CAPABILITIES)

    # ── Render CLAUDE.md ──────────────────────────────────────────────────────

    def render_claude_md(
        self,
        spec: AgentSpec,
        plan: PlanV1,
        extra_capabilities: list[str] | None = None,
    ) -> str:
        """
        Compose CLAUDE.md dari:
        1. Base template (atau fallback sederhana)
        2. Archetype fragment
        3. Capability packs (inferred dari tools + extra_capabilities)
        """
        capabilities = self.infer_capabilities(spec.tools_required)
        if extra_capabilities:
            capabilities = sorted(set(capabilities) | set(extra_capabilities))

        # Base section
        base_content = self._render_base(spec, plan)

        # Archetype section
        archetype_frag = self.load_archetype_fragment(spec.archetype)
        archetype_section = f"\n{archetype_frag}\n" if archetype_frag else ""

        # Capability sections
        packs = self.load_capability_packs(capabilities)
        capability_sections = ""
        if packs:
            capability_sections = "\n" + "\n\n".join(p.content for p in packs) + "\n"

        return base_content + archetype_section + capability_sections

    def _render_base(self, spec: AgentSpec, plan: PlanV1) -> str:
        """Render base CLAUDE.md — Jinja2 jika tersedia, fallback ke string format."""
        if self._jinja_env is not None:
            try:
                template = self._jinja_env.get_template("_base/CLAUDE.md.j2")
                ctx = _make_template_context(spec, plan)
                return template.render(ctx)
            except (TemplateNotFound, Exception) as e:
                logger.warning(f"templates: Jinja2 render gagal, fallback: {e}")

        return _fallback_claude_md(spec, plan)

    def render_manifest_yaml(self, spec: AgentSpec, plan: PlanV1) -> dict[str, Any]:
        """
        Return manifest dict. Jinja2 template dipakai untuk display saja;
        factory.py serialize ke YAML via yaml.dump().
        """
        return _fallback_manifest(spec, plan)


# ── Template context ──────────────────────────────────────────────────────────

def _make_template_context(spec: AgentSpec, plan: PlanV1) -> dict[str, Any]:
    """Build Jinja2 context dari AgentSpec + PlanV1."""

    class _Agent:
        name = spec.name
        role_description = spec.role_description
        tone = "professional, concise, helpful"
        language = "Indonesia"
        archetype = spec.archetype.value
        constraints = [
            "Hanya baca/tulis di direktori memory agent kamu sendiri kecuali ada izin eksplisit.",
            "Kalau tidak yakin atau ada edge case tidak terduga, eskalasi ke EL SOLVER.",
        ]
        tools = [type("T", (), {"name": t, "description": ""})() for t in spec.tools_required]
        memory = type("M", (), {
            "always": ["CLAUDE.md", "memory/agent-log.md"],
            "scopes": spec.memory_scopes or ["agent"],
        })()
        escalation_policy = (
            "Kalau task ambigu, risiko tinggi, atau butuh keputusan Wildan → "
            "tulis ke memory/escalations.md dan hentikan eksekusi."
        )
        examples: list = []

    class _User:
        bio = "Wildan, tour leader & content creator"

    return {"agent": _Agent(), "user": _User()}


# ── Fallback renderers (jika Jinja2 tidak tersedia) ──────────────────────────

def _fallback_claude_md(spec: AgentSpec, plan: PlanV1) -> str:
    """String-based CLAUDE.md renderer (sama dengan factory.py lama)."""
    from datetime import datetime, timezone
    tools_section = "\n".join(f"- `{t}`" for t in spec.tools_required) or "- (tidak ada tool khusus)"
    memory_section = "\n".join(f"- {s}" for s in spec.memory_scopes) or "- agent"

    return textwrap.dedent(f"""\
        # Agent: {spec.name}

        ## Role
        {spec.role_description}

        ## Identitas
        - Kamu adalah agent khusus yang dibuat oleh EL SOLVER untuk Wildan.
        - Bahasa: Indonesia. Langsung ke poin, tidak basa-basi.
        - Jangan keluar dari scope role kamu.

        ## Arsitektur: {spec.archetype.value}

        ## Tools yang tersedia
        {tools_section}

        ## Memory access
        {memory_section}

        ## Constraints
        - Hanya baca/tulis di direktori memory agent kamu sendiri kecuali ada izin eksplisit.
        - Kalau tidak yakin atau ada edge case tidak terduga, eskalasi ke EL SOLVER.
        - Log setiap aksi penting ke memory/agent-log.md.

        ## Eskalasi
        Kalau task ambigu, risiko tinggi, atau butuh keputusan Wildan → tulis ke memory/escalations.md dan hentikan eksekusi.
    """)


def _fallback_manifest(spec: AgentSpec, plan: PlanV1) -> dict[str, Any]:
    from datetime import datetime, timezone
    return {
        "name": spec.name,
        "version": "0.1.0",
        "archetype": spec.archetype.value,
        "role": spec.role_description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_from_plan": plan.request_summary,
        "tools": {
            "allowed": spec.tools_required,
            "denied": ["shell.execute"],
        },
        "memory": {
            "always_load": ["CLAUDE.md", "memory/agent-log.md"],
            "scopes": spec.memory_scopes or ["agent"],
        },
        "schedule": spec.schedule,
        "trigger": spec.trigger,
        "risk_level": plan.risk_level,
        "status": "created",
    }
