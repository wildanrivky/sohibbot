"""
Agent Factory — materialize PlanV1 jadi folder agent nyata.

Input : PlanV1 dari planner
Output: folder /agents/<name>/ berisi:
  - CLAUDE.md      (system prompt agent)
  - manifest.yaml  (metadata: tools, memory, schedule, version)
  - run.py         (entry point: panggil claude CLI dengan context agent)
  - memory/        (direktori memory slice agent)
  - tests/         (direktori smoke tests — diisi Phase 2)

Factory tidak deploy — hanya materialisasi file. Deploy ada di registry/launchagent.
"""
from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from el_solver.core.planner import AgentArchetype, AgentSpec, PlanV1
from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger
from el_solver.core.smoke import (
    SmokeTestFailed,
    SmokeTestSuite,
    generate_default_cases,
    run_smoke_suite,
)
from el_solver.core.templates import TemplateRenderer

_renderer = TemplateRenderer()

logger = get_logger(__name__)

# Direktori root agents/ relatif terhadap project root
AGENTS_DIR = PROJECT_ROOT / "agents"


# ── Errors ────────────────────────────────────────────────────────────────────

class FactoryError(Exception):
    """Gagal materialize agent."""


# ── Template generators ───────────────────────────────────────────────────────

def _render_claude_md(spec: AgentSpec, plan: PlanV1) -> str:
    tools_section = "\n".join(f"- `{t}`" for t in spec.tools_required) or "- (tidak ada tool khusus)"
    memory_section = "\n".join(f"- {s}" for s in spec.memory_scopes) or "- agent"

    schedule_note = ""
    if spec.archetype == AgentArchetype.SCHEDULED and spec.schedule:
        schedule_note = f"\n**Jadwal**: `{spec.schedule}` (cron)"
    elif spec.archetype == AgentArchetype.REACTIVE and spec.trigger:
        schedule_note = f"\n**Trigger**: event `{spec.trigger}`"

    steps_section = ""
    if plan.steps:
        steps_lines = "\n".join(
            f"{i+1}. [{s.type.value}] {s.description}"
            for i, s in enumerate(plan.steps)
        )
        steps_section = f"\n## Alur Eksekusi\n\n{steps_lines}\n"

    return textwrap.dedent(f"""\
        # Agent: {spec.name}

        ## Role
        {spec.role_description}

        ## Identitas
        - Kamu adalah agent khusus yang dibuat oleh EL SOLVER untuk Wildan.
        - Bahasa: Indonesia. Langsung ke poin, tidak basa-basi.
        - Jangan keluar dari scope role kamu.{schedule_note}

        ## Arsitektur: {spec.archetype.value}

        ## Tools yang tersedia
        {tools_section}

        ## Memory access
        {memory_section}

        ## Constraints
        - Hanya baca/tulis di direktori memory agent kamu sendiri kecuali ada izin eksplisit.
        - Kalau tidak yakin atau ada edge case tidak terduga, eskalasi ke EL SOLVER.
        - Log setiap aksi penting ke memory/agent-log.md.
        {steps_section}
        ## Eskalasi
        Kalau task ambigu, risiko tinggi, atau butuh keputusan Wildan → tulis ke memory/escalations.md dan hentikan eksekusi.
    """)


def _render_manifest(spec: AgentSpec, plan: PlanV1) -> dict:
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


def _render_run_py(spec: AgentSpec) -> str:
    return textwrap.dedent(f"""\
        #!/usr/bin/env python
        \"\"\"Entry point untuk agent: {spec.name}\"\"\"
        from __future__ import annotations

        import subprocess
        import sys
        from pathlib import Path

        AGENT_DIR = Path(__file__).parent.resolve()
        CLAUDE_MD = AGENT_DIR / "CLAUDE.md"


        def run(message: str) -> str:
            \"\"\"Jalankan agent dengan pesan input, return response.\"\"\"
            import os
            from el_solver.config import settings

            env = {{k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}}
            cmd = [
                settings.claude_cli_path,
                "-p", message,
                "--dangerously-skip-permissions",
            ]
            if settings.claude_model_default:
                cmd.extend(["--model", settings.claude_model_default])

            result = subprocess.run(
                cmd,
                cwd=str(AGENT_DIR),
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Agent CLI error: {{result.stderr.strip()}}")
            return result.stdout.strip()


        def main() -> None:
            if len(sys.argv) < 2:
                print(f"Usage: python run.py <message>", file=sys.stderr)
                sys.exit(1)
            message = " ".join(sys.argv[1:])
            print(run(message))


        if __name__ == "__main__":
            main()
    """)


# ── Core factory function ─────────────────────────────────────────────────────

def materialize(
    plan: PlanV1,
    agents_dir: Optional[Path] = None,
    overwrite: bool = False,
    smoke_test: bool = False,
    smoke_timeout: int = 60,
) -> list[Path]:
    """
    Materialize semua agent dalam PlanV1 ke filesystem.

    Args:
        plan          : PlanV1 dari planner
        agents_dir    : override direktori agents/ (default: PROJECT_ROOT/agents/)
        overwrite     : kalau True, timpa agent yang sudah ada
        smoke_test    : kalau True, jalankan smoke test setelah materialize
        smoke_timeout : timeout per smoke test case (detik)

    Returns:
        List path folder agent yang dibuat

    Raises:
        FactoryError    : kalau agent sudah ada dan overwrite=False,
                          atau plan tidak punya agent
        SmokeTestFailed : kalau smoke_test=True dan ada test yang gagal
    """
    if not plan.agents:
        raise FactoryError("Plan tidak punya agent untuk di-materialize.")

    base = agents_dir or AGENTS_DIR
    base.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    for spec in plan.agents:
        agent_dir = _materialize_one(spec, plan, base, overwrite)
        created.append(agent_dir)

        if smoke_test:
            cases = generate_default_cases(spec)
            suite = run_smoke_suite(agent_dir, cases, timeout=smoke_timeout)
            if not suite.passed:
                raise SmokeTestFailed(suite)

    logger.info(f"factory: {len(created)} agent(s) created: {[p.name for p in created]}")
    return created


def _materialize_one(
    spec: AgentSpec,
    plan: PlanV1,
    base: Path,
    overwrite: bool,
) -> Path:
    agent_dir = base / spec.name

    if agent_dir.exists() and not overwrite:
        raise FactoryError(
            f"Agent '{spec.name}' sudah ada di {agent_dir}. "
            "Gunakan overwrite=True atau maintain_agent mode untuk update."
        )

    # Buat struktur folder
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "memory").mkdir(exist_ok=True)
    (agent_dir / "tests").mkdir(exist_ok=True)

    # CLAUDE.md — via TemplateRenderer (Jinja2 + capability packs)
    (agent_dir / "CLAUDE.md").write_text(
        _renderer.render_claude_md(spec, plan), encoding="utf-8"
    )

    # manifest.yaml
    manifest_data = _renderer.render_manifest_yaml(spec, plan)
    with open(agent_dir / "manifest.yaml", "w", encoding="utf-8") as f:
        yaml.dump(manifest_data, f, default_flow_style=False, allow_unicode=True)

    # run.py
    run_py = agent_dir / "run.py"
    run_py.write_text(_render_run_py(spec), encoding="utf-8")
    run_py.chmod(0o755)

    # memory/agent-log.md — seed file
    log_path = agent_dir / "memory" / "agent-log.md"
    log_path.write_text(
        f"# Agent Log — {spec.name}\n\nDibuat: {datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )

    logger.info(f"factory: materialized agent '{spec.name}' → {agent_dir}")
    return agent_dir


def agent_exists(name: str, agents_dir: Optional[Path] = None) -> bool:
    """Cek apakah agent dengan nama ini sudah ada."""
    base = agents_dir or AGENTS_DIR
    return (base / name).is_dir()


def list_agents(agents_dir: Optional[Path] = None) -> list[str]:
    """List semua agent yang sudah ter-materialize."""
    base = agents_dir or AGENTS_DIR
    if not base.exists():
        return []
    return sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and not d.name.startswith("_") and (d / "manifest.yaml").exists()
    )
