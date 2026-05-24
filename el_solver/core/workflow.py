"""
Workflow Engine — eksekusi multi-step workflow YAML dengan sequential/parallel.

Workflow didefinisikan sebagai YAML (contoh di workflows/).
Engine:
1. Parse YAML → WorkflowDefinition
2. Resolve step dependencies → DAG
3. Eksekusi: sequential default, parallel kalau step tidak ada dependency
4. Template substitution untuk input antar step (Jinja2)
5. Persist state ke SQLite (workflow_runs + workflow_step_results)

Usage:
    engine = WorkflowEngine()
    run_id = engine.run("content-repurposing", {"blog_url": "https://..."})
    result = engine.get_run(run_id)
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, Undefined

from el_solver.config import PROJECT_ROOT
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

WORKFLOWS_DIR = PROJECT_ROOT / "workflows"
MAX_WORKFLOW_DEPTH = 3
DEFAULT_STEP_TIMEOUT = 300  # detik


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class WorkflowStep:
    id: str
    agent: str
    input_template: str
    output_key: str
    depends_on: list[str] = field(default_factory=list)
    timeout: int = DEFAULT_STEP_TIMEOUT
    parallel: bool = False


@dataclass
class WorkflowDefinition:
    name: str
    description: str
    steps: list[WorkflowStep]

    def step_by_id(self, step_id: str) -> WorkflowStep | None:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None


@dataclass
class StepResult:
    step_id: str
    agent: str
    status: str  # success | error | skipped | timeout
    output: str = ""
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class WorkflowRun:
    run_id: str
    workflow_name: str
    status: str  # running | success | error | timeout
    started_at: str
    finished_at: str | None
    step_results: list[StepResult]
    context: dict[str, Any]
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


# ── YAML Parser ───────────────────────────────────────────────────────────────

def load_workflow(name: str, workflows_dir: Path | None = None) -> WorkflowDefinition:
    """Load dan parse YAML workflow definition."""
    base = workflows_dir or WORKFLOWS_DIR
    path = base / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Workflow '{name}' tidak ditemukan: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps: list[WorkflowStep] = []

    for step_data in raw.get("steps", []):
        steps.append(WorkflowStep(
            id=step_data["id"],
            agent=step_data.get("agent", ""),
            input_template=step_data.get("input", ""),
            output_key=step_data.get("output_key", step_data["id"]),
            depends_on=step_data.get("depends_on", []),
            timeout=step_data.get("timeout", DEFAULT_STEP_TIMEOUT),
            parallel=step_data.get("parallel", False),
        ))

    return WorkflowDefinition(
        name=raw.get("name", name),
        description=raw.get("description", ""),
        steps=steps,
    )


def load_all_workflows(workflows_dir: Path | None = None) -> dict[str, WorkflowDefinition]:
    """Load semua workflow dari direktori."""
    base = workflows_dir or WORKFLOWS_DIR
    if not base.exists():
        return {}
    result: dict[str, WorkflowDefinition] = {}
    for yaml_file in sorted(base.glob("*.yaml")):
        try:
            wf = load_workflow(yaml_file.stem, base)
            result[wf.name] = wf
        except Exception as e:
            logger.warning(f"workflow: gagal load {yaml_file.name}: {e}")
    return result


# ── DAG resolver ──────────────────────────────────────────────────────────────

def _topological_sort(steps: list[WorkflowStep]) -> list[list[WorkflowStep]]:
    """
    Return list of "layers" — tiap layer bisa dieksekusi parallel.
    Layer N harus selesai sebelum layer N+1 dimulai.
    """
    step_map = {s.id: s for s in steps}
    in_degree: dict[str, int] = {s.id: 0 for s in steps}

    # Hitung dependency dari YAML + implicit (step N bergantung pada N-1 secara default)
    # Kalau step tidak punya depends_on → gunakan urutan sequential (depends on sebelumnya)
    for i, step in enumerate(steps):
        if step.depends_on:
            for dep in step.depends_on:
                in_degree[step.id] += 1
        elif i > 0 and not steps[i - 1].parallel:
            in_degree[step.id] += 1

    available = [s for s in steps if in_degree[s.id] == 0]
    layers: list[list[WorkflowStep]] = []

    while available:
        # Layer: semua step yang in_degree == 0
        layer = list(available)
        layers.append(layer)
        available = []

        for done in layer:
            # Cari step yang bergantung pada yang baru selesai
            for step in steps:
                if done.id in step.depends_on:
                    in_degree[step.id] -= 1
                    if in_degree[step.id] == 0:
                        available.append(step)
                # Sequential: step setelah step yang parallel=False juga unlock
                idx = steps.index(step)
                if idx > 0 and steps[idx - 1].id == done.id and not done.parallel:
                    if not step.depends_on:
                        in_degree[step.id] -= 1
                        if in_degree[step.id] == 0 and step not in available:
                            available.append(step)

    return layers


# ── Template resolver ─────────────────────────────────────────────────────────

_jinja = Environment(undefined=Undefined)  # lenient — missing var → empty string


def _resolve_input(template: str, context: dict[str, Any]) -> str:
    """Render Jinja2 template dengan context (step outputs + initial vars)."""
    try:
        return _jinja.from_string(template).render(**context)
    except Exception as e:
        logger.warning(f"workflow: template render gagal: {e}")
        return template  # fallback ke raw template


# ── Agent invoker ─────────────────────────────────────────────────────────────

def _invoke_agent_sync(
    agent_name: str,
    message: str,
    agent_dir_base: Path | None = None,
    timeout: int = DEFAULT_STEP_TIMEOUT,
) -> tuple[str, str | None]:
    """
    Invoke agent via run.py. Return (output, error).
    Jika agent tidak ada → return ("", error_message).
    """
    import subprocess
    import sys

    base = agent_dir_base or (PROJECT_ROOT / "agents")
    run_py = base / agent_name / "run.py"

    if not run_py.exists():
        return "", f"Agent '{agent_name}' tidak ditemukan: {run_py}"

    try:
        result = subprocess.run(
            [sys.executable, str(run_py), message],
            cwd=str(run_py.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return "", f"exit {result.returncode}: {result.stderr.strip()}"
        return result.stdout.strip(), None
    except subprocess.TimeoutExpired:
        return "", f"timeout setelah {timeout}s"
    except Exception as e:
        return "", str(e)


# ── DB persistence ────────────────────────────────────────────────────────────

def _db_create_run(run_id: str, workflow_name: str, context: dict[str, Any]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO workflow_runs (run_id, workflow_name, status, input_context) "
            "VALUES (?, ?, 'running', ?)",
            (run_id, workflow_name, json.dumps(context, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def _db_finish_run(run_id: str, status: str, error: str | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE workflow_runs SET status=?, finished_at=CURRENT_TIMESTAMP, error=? "
            "WHERE run_id=?",
            (status, error, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _db_save_step(
    run_id: str,
    step_id: str,
    agent: str,
    status: str,
    input_snapshot: str,
    output: str,
    error: str | None,
    duration_ms: float,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO workflow_step_results
               (run_id, step_id, agent_name, status, input_snapshot, output, error,
                started_at, finished_at, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)""",
            (run_id, step_id, agent, status, input_snapshot, output, error, duration_ms),
        )
        conn.commit()
    finally:
        conn.close()


# ── Workflow Engine ────────────────────────────────────────────────────────────

class WorkflowEngine:
    """
    Eksekusi workflow multi-step.

    Setiap step memanggil agent via run.py.
    Output step tersedia untuk step berikutnya via Jinja2 context.
    """

    def __init__(
        self,
        workflows_dir: Path | None = None,
        agent_dir_base: Path | None = None,
    ) -> None:
        self.workflows_dir = workflows_dir or WORKFLOWS_DIR
        self.agent_dir_base = agent_dir_base or (PROJECT_ROOT / "agents")

    def run(
        self,
        workflow_name: str,
        context: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> WorkflowRun:
        """
        Jalankan workflow synchronous.

        Args:
            workflow_name : nama file YAML (tanpa .yaml)
            context       : initial variables (e.g. {"blog_url": "https://..."})
            dry_run       : kalau True, resolve steps tapi tidak invoke agent

        Returns:
            WorkflowRun dengan semua step results
        """
        run_id = str(uuid.uuid4())[:8]
        ctx = dict(context or {})
        started_at = datetime.now(timezone.utc).isoformat()

        logger.info(f"workflow[{workflow_name}]: starting run {run_id}")

        try:
            wf = load_workflow(workflow_name, self.workflows_dir)
        except FileNotFoundError as e:
            return WorkflowRun(
                run_id=run_id,
                workflow_name=workflow_name,
                status="error",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                step_results=[],
                context=ctx,
                error=str(e),
            )

        _db_create_run(run_id, workflow_name, ctx)
        step_results: list[StepResult] = []
        layers = _topological_sort(wf.steps)

        overall_status = "success"
        overall_error = None

        for layer in layers:
            # Eksekusi tiap layer (bisa parallel di masa depan — sekarang sequential)
            for step in layer:
                result = self._execute_step(
                    run_id=run_id,
                    step=step,
                    context=ctx,
                    dry_run=dry_run,
                )
                step_results.append(result)

                # Simpan output ke context untuk step berikutnya
                if result.status == "success":
                    ctx[step.output_key] = result.output
                    # Juga simpan di steps.<id>.output untuk template akses
                    if "steps" not in ctx:
                        ctx["steps"] = {}
                    ctx["steps"].setdefault(step.id, {})["output"] = {
                        step.output_key: result.output
                    }
                else:
                    overall_status = "error"
                    overall_error = f"Step '{step.id}' gagal: {result.error}"
                    logger.error(f"workflow[{workflow_name}]: {overall_error}")
                    break  # stop on first failure

            if overall_status == "error":
                break

        _db_finish_run(run_id, overall_status, overall_error)
        finished_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"workflow[{workflow_name}]: run {run_id} finished → {overall_status} "
            f"({len(step_results)} steps)"
        )

        return WorkflowRun(
            run_id=run_id,
            workflow_name=workflow_name,
            status=overall_status,
            started_at=started_at,
            finished_at=finished_at,
            step_results=step_results,
            context=ctx,
            error=overall_error,
        )

    def _execute_step(
        self,
        run_id: str,
        step: WorkflowStep,
        context: dict[str, Any],
        dry_run: bool,
    ) -> StepResult:
        import time

        resolved_input = _resolve_input(step.input_template, context)
        logger.info(f"workflow: step '{step.id}' → agent '{step.agent}'")

        start = time.time()

        if dry_run:
            output, error = f"[dry_run] {resolved_input[:100]}", None
            duration_ms = 0.0
        else:
            output, error = _invoke_agent_sync(
                step.agent,
                resolved_input,
                self.agent_dir_base,
                step.timeout,
            )
            duration_ms = round((time.time() - start) * 1000, 2)

        status = "success" if error is None else "error"
        _db_save_step(
            run_id, step.id, step.agent, status,
            resolved_input[:1000], output[:2000], error, duration_ms,
        )

        return StepResult(
            step_id=step.id,
            agent=step.agent,
            status=status,
            output=output,
            error=error or "",
            duration_ms=duration_ms,
        )

    def get_run(self, run_id: str) -> WorkflowRun | None:
        """Baca satu workflow run dari DB."""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT run_id, workflow_name, status, input_context, "
                "started_at, finished_at, error FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None

            step_rows = conn.execute(
                "SELECT step_id, agent_name, status, output, error, duration_ms "
                "FROM workflow_step_results WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()

            return WorkflowRun(
                run_id=row["run_id"],
                workflow_name=row["workflow_name"],
                status=row["status"],
                started_at=str(row["started_at"]),
                finished_at=row["finished_at"],
                step_results=[
                    StepResult(
                        step_id=r["step_id"],
                        agent=r["agent_name"],
                        status=r["status"],
                        output=r["output"] or "",
                        error=r["error"] or "",
                        duration_ms=r["duration_ms"] or 0.0,
                    )
                    for r in step_rows
                ],
                context=json.loads(row["input_context"] or "{}"),
                error=row["error"],
            )
        finally:
            conn.close()

    def list_runs(self, workflow_name: str | None = None, limit: int = 20) -> list[dict]:
        """List workflow runs dari DB (recent first)."""
        conn = get_connection()
        try:
            if workflow_name:
                rows = conn.execute(
                    "SELECT run_id, workflow_name, status, started_at, finished_at "
                    "FROM workflow_runs WHERE workflow_name=? "
                    "ORDER BY started_at DESC LIMIT ?",
                    (workflow_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT run_id, workflow_name, status, started_at, finished_at "
                    "FROM workflow_runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
