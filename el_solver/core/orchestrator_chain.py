"""Execution engine untuk chain delegasi multi-agent."""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from el_solver.core.orchestrator import IntentResult, Mode
from el_solver.core.planner import PlanStep, PlanV1
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "el-solver.db"


@dataclass
class ChainStepResult:
    step_id: str
    child_task_id: str
    agent_assignee: str
    status: str
    output_summary: str = ""
    delegation_id: str | None = None
    error: str | None = None


@dataclass
class ChainResult:
    parent_task_id: str
    status: str
    summary: str
    step_results: list[ChainStepResult] = field(default_factory=list)


def _short_preview(text: str, limit: int = 200) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip()


def _topological_order(steps: list[PlanStep]) -> list[PlanStep]:
    by_id = {step.id: step for step in steps}
    temp: set[str] = set()
    visited: set[str] = set()
    ordered: list[PlanStep] = []

    def dfs(step_id: str) -> None:
        if step_id in visited:
            return
        if step_id in temp:
            raise ValueError(f"Cycle dependency detected at {step_id}")
        temp.add(step_id)
        step = by_id[step_id]
        for dep in step.depends_on:
            if dep not in by_id:
                raise ValueError(f"Dependency tidak ditemukan: {dep}")
            dfs(dep)
        temp.remove(step_id)
        visited.add(step_id)
        ordered.append(step)

    for step in steps:
        dfs(step.id)

    return ordered


def _insert_delegation(
    parent_task_id: str,
    parent_agent: str | None,
    child_agent: str,
    step_order: int,
    step_description: str,
    context_in: str,
    child_task_id: str,
) -> str:
    delegation_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO delegations
               (id, parent_task_id, child_task_id, parent_agent, child_agent, step_order,
                step_description, status, context_in)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                delegation_id,
                parent_task_id,
                child_task_id,
                parent_agent,
                child_agent,
                step_order,
                step_description,
                "running",
                context_in,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return delegation_id


def _update_delegation(
    delegation_id: str,
    status: str,
    output_summary: str = "",
    finished_at: datetime | None = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE delegations
               SET status=?, output_summary=?, finished_at=?
               WHERE id=?""",
            (
                status,
                output_summary,
                (finished_at or datetime.now(timezone.utc)).isoformat(),
                delegation_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _deps_context(outputs_by_step: dict[str, str], step: PlanStep) -> str:
    chunks = [outputs_by_step[dep] for dep in step.depends_on if dep in outputs_by_step]
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _build_capability_resolver() -> tuple[set[str], object | None]:
    """Return (known_agent_names, capability_graph_or_None).

    Best-effort: if the agent mesh / graph can't be loaded the chain falls
    back to the legacy literal-name behaviour.
    """
    try:
        from el_solver.agents.base import scan_agents
        from el_solver.config import PROJECT_ROOT
        from el_solver.core.capability_graph import load_graph_with_agents

        agents_dir = PROJECT_ROOT / "agents"
        known = {info.name for info in scan_agents(agents_dir)}
        graph = load_graph_with_agents(agents_dir)
        return known, graph
    except Exception as exc:  # noqa: BLE001 — delegation must still work
        logger.warning(f"orchestrator_chain: capability resolver unavailable ({exc})")
        return set(), None


def _resolve_step_agent(
    step: PlanStep,
    known_agents: set[str],
    graph: object | None,
) -> str:
    """Resolve a step to a concrete agent without hard-coded routing.

    Order: explicit known agent → capability/skill id via graph →
    legacy literal pass-through → ``el-solver``. Backward compatible:
    a step that already names a real agent resolves to that same agent.
    """
    raw = step.agent_assignee or step.tool_or_agent
    if not raw:
        return "el-solver"
    if raw in known_agents:
        return raw
    if graph is not None:
        try:
            resolved = graph.find_agent_for_skill(raw)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            resolved = None
        if resolved:
            return resolved
    return raw


async def execute_chain(
    plan: PlanV1,
    parent_task_id: str,
    channel: str,
    user_id: str,
) -> ChainResult:
    """Execute chain steps secara topological dan catat delegations."""
    ordered_steps = _topological_order(plan.steps)
    outputs_by_step: dict[str, str] = {}
    step_results: list[ChainStepResult] = []
    overall_status = "completed"
    parent_agent = plan.agents[0].name if plan.agents else None

    from el_solver.channels.handler import _invoke_agent_handler

    known_agents, capability_graph = _build_capability_resolver()

    for idx, step in enumerate(ordered_steps, start=1):
        agent_name = _resolve_step_agent(step, known_agents, capability_graph)
        context_in = _deps_context(outputs_by_step, step)
        if context_in:
            step_input = f"{step.description}\n\nKonteks dependensi:\n{context_in}"
        else:
            step_input = step.description

        child_task_id = str(uuid.uuid4())
        delegation_id = _insert_delegation(
            parent_task_id=parent_task_id,
            parent_agent=parent_agent,
            child_agent=agent_name,
            step_order=idx,
            step_description=step.description,
            context_in=context_in,
            child_task_id=child_task_id,
        )

        try:
            intent = IntentResult(
                mode=Mode.INVOKE_AGENT,
                confidence=1.0,
                raw_message=step_input,
                agent_name=agent_name,
                method="orchestrate",
                extras={
                    "parent_task_id": parent_task_id,
                    "child_task_id": child_task_id,
                    "delegation_id": delegation_id,
                    "step_id": step.id,
                },
            )
            response = await _invoke_agent_handler(
                intent,
                channel=channel,
                user_id=user_id,
                run_id=parent_task_id,
            )
            output_text = response.text or ""
            output_summary = response.metadata.get("agent_result_summary") or _short_preview(output_text)
            outputs_by_step[step.id] = output_summary
            _update_delegation(delegation_id, "completed", output_summary)

            try:
                from el_solver.core.events import emit_event
                emit_event(
                    "delegation.completed",
                    {
                        "delegation_id": delegation_id,
                        "step_id": step.id,
                        "child_task_id": child_task_id,
                        "status": "completed",
                    },
                    agent=agent_name,
                    task_id=child_task_id,
                    run_id=parent_task_id,
                )
            except Exception:
                pass

            step_results.append(
                ChainStepResult(
                    step_id=step.id,
                    child_task_id=child_task_id,
                    agent_assignee=agent_name,
                    status="completed",
                    output_summary=output_summary,
                    delegation_id=delegation_id,
                )
            )
        except Exception as exc:
            error_text = str(exc)
            output_summary = _short_preview(error_text)
            _update_delegation(delegation_id, "error", output_summary)
            try:
                from el_solver.core.events import emit_event
                emit_event(
                    "delegation.completed",
                    {
                        "delegation_id": delegation_id,
                        "step_id": step.id,
                        "child_task_id": child_task_id,
                        "status": "error",
                        "error": output_summary,
                    },
                    agent=agent_name,
                    task_id=child_task_id,
                    run_id=parent_task_id,
                )
            except Exception:
                pass

            step_results.append(
                ChainStepResult(
                    step_id=step.id,
                    child_task_id=child_task_id,
                    agent_assignee=agent_name,
                    status="error",
                    output_summary=output_summary,
                    delegation_id=delegation_id,
                    error=error_text,
                )
            )

            if not step.optional:
                overall_status = "partial"
                break

        if step_results and step_results[-1].status == "error" and step.optional:
            overall_status = "partial"

    if any(result.status == "error" for result in step_results) and overall_status == "completed":
        overall_status = "partial"

    completed = sum(1 for result in step_results if result.status == "completed")
    summary = (
        f"Delegation chain {overall_status}: {completed}/{len(ordered_steps)} langkah selesai."
        if ordered_steps
        else "Tidak ada langkah delegasi."
    )
    return ChainResult(
        parent_task_id=parent_task_id,
        status=overall_status,
        summary=summary,
        step_results=step_results,
    )
