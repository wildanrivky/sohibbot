"""Task decomposition into delegated agent steps."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from el_solver.core.capability_graph import CapabilityGraph, load_default_graph
from el_solver.core.orchestrator import IntentResult
from el_solver.core.planner import AgentArchetype, AgentSpec, PlanError, PlanStep, PlanV1, StepType
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DecompositionDraft:
    reasoning: str
    steps: list[dict[str, Any]]


def _extract_json(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return match.group(0)
    raise PlanError("Decomposer response tidak berisi JSON")


def _parse_decomposition(text: str) -> DecompositionDraft:
    data = json.loads(_extract_json(text))
    steps = data.get("steps") or []
    if not isinstance(steps, list):
        raise PlanError("Format steps tidak valid")
    return DecompositionDraft(
        reasoning=str(data.get("reasoning") or ""),
        steps=[step for step in steps if isinstance(step, dict)],
    )


def _agent_spec_from_name(agent_name: str, graph: CapabilityGraph | None = None) -> AgentSpec:
    from el_solver.core.registry import AgentRegistry

    reg = AgentRegistry()
    record = reg.get(agent_name)
    if record:
        manifest = record.manifest or {}
        tools_required = manifest.get("tools", []) if isinstance(manifest, dict) else []
        return AgentSpec(
            name=record.name,
            archetype=AgentArchetype(record.archetype) if record.archetype in AgentArchetype._value2member_map_ else AgentArchetype.CONVERSATIONAL,
            role_description=record.role_description or (graph.nodes.get(agent_name).description if graph and agent_name in graph.nodes else ""),
            tools_required=[str(item) for item in tools_required if str(item).strip()],
            memory_scopes=[str(item) for item in (manifest.get("memory_scopes", []) if isinstance(manifest, dict) else []) if str(item).strip()],
        )

    return AgentSpec(
        name=agent_name,
        archetype=AgentArchetype.CONVERSATIONAL,
        role_description=agent_name,
        tools_required=[],
        memory_scopes=["agent"],
    )


def _fallback_agent_for_task(task_text: str, graph: CapabilityGraph | None = None) -> str:
    if graph is None:
        graph = load_default_graph()

    from el_solver.channels.handler import _match_agent_for_task
    agent_name, _ = _match_agent_for_task(task_text, "")
    if agent_name:
        return agent_name

    provider_candidates = [name for name in graph.skill_ids() if graph.find_agent_for_skill(name)]
    if provider_candidates:
        return graph.find_agent_for_skill(provider_candidates[0]) or provider_candidates[0]

    from el_solver.core.registry import AgentRegistry

    reg = AgentRegistry()
    active = reg.list_active()
    if active:
        return active[0].name
    all_agents = reg.list_all()
    if all_agents:
        return all_agents[0].name

    return "el-solver"


def _validate_agent_assignees(steps: list[dict[str, Any]], graph: CapabilityGraph) -> None:
    for step in steps:
        assignee = str(step.get("agent_assignee") or "").strip()
        if not assignee:
            raise PlanError("agent_assignee kosong")
        if not any(agent for agent in graph.describe_agents().splitlines() if agent.startswith(f"- {assignee}:")):
            from el_solver.core.registry import AgentRegistry
            reg = AgentRegistry()
            if not reg.get(assignee):
                raise PlanError(f"Agent assignee tidak ditemukan: {assignee}")


def _validate_dependencies(steps: list[dict[str, Any]]) -> None:
    ids = [str(step.get("step_id") or step.get("id") or f"step-{idx + 1}") for idx, step in enumerate(steps)]
    by_id = {}
    for idx, step_id in enumerate(ids):
        if step_id in by_id:
            raise PlanError(f"Duplicate step_id: {step_id}")
        by_id[step_id] = idx

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(step_id: str) -> None:
        if step_id in visiting:
            raise PlanError(f"Cycle dependency detected at {step_id}")
        if step_id in visited:
            return
        visiting.add(step_id)
        step = steps[by_id[step_id]]
        for dep in step.get("depends_on", []) or []:
            dep_id = str(dep)
            if dep_id not in by_id:
                raise PlanError(f"Dependency tidak ditemukan: {dep_id}")
            dfs(dep_id)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in ids:
        dfs(step_id)


def _build_plan_from_steps(
    intent: IntentResult,
    steps: list[dict[str, Any]],
    graph: CapabilityGraph,
    raw_llm_response: str = "",
    reasoning: str = "",
) -> PlanV1:
    normalized_steps: list[PlanStep] = []
    agents: dict[str, AgentSpec] = {}

    for idx, step in enumerate(steps, start=1):
        step_id = str(step.get("step_id") or step.get("id") or f"step-{idx}")
        assignee = str(step.get("agent_assignee") or step.get("agent") or "").strip()
        depends_on = [str(dep).strip() for dep in (step.get("depends_on") or []) if str(dep).strip()]
        description = str(step.get("description") or step.get("task") or step.get("summary") or "").strip()
        optional = bool(step.get("optional", False))
        if not description:
            description = f"Delegated step {idx}"

        normalized_steps.append(
            PlanStep(
                id=step_id,
                type=StepType.AGENT_CALL,
                description=description,
                tool_or_agent=assignee,
                agent_assignee=assignee,
                depends_on=depends_on,
                optional=optional,
            )
        )

        if assignee and assignee not in agents:
            agents[assignee] = _agent_spec_from_name(assignee, graph)

    return PlanV1(
        version="1",
        request_summary=intent.raw_message[:120],
        mode="orchestrate",
        agents=list(agents.values()),
        steps=normalized_steps,
        tools_needed=[],
        risk_level="L1",
        clarification_needed=False,
        clarification_questions=[],
        raw_llm_response=raw_llm_response or reasoning,
    )


def _fallback_plan(intent: IntentResult, graph: CapabilityGraph) -> PlanV1:
    from el_solver.core.orchestrator import Mode
    # For any mode, produce a simple 1-step fallback plan. Previously the
    # function raised for Mode.CONVERSATION which makes callers unable to
    # recover from bad LLM decomposition output (tests expect a fallback
    # plan). Returning a single-step plan is safer and preserves behaviour
    # expected by existing test-suite.
    # NOTE: this is intentionally permissive — orchestration callers may
    # still treat conversation-mode specially upstream.

    agent_name = _fallback_agent_for_task(intent.raw_message, graph)
    return PlanV1(
        version="1",
        request_summary=intent.raw_message[:120],
        mode="orchestrate",
        agents=[_agent_spec_from_name(agent_name, graph)],
        steps=[
            PlanStep(
                id="step-1",
                type=StepType.AGENT_CALL,
                description=intent.raw_message[:160],
                tool_or_agent=agent_name,
                agent_assignee=agent_name,
                depends_on=[],
                optional=False,
            )
        ],
        tools_needed=[],
        risk_level="L1",
        clarification_needed=False,
        clarification_questions=[],
        raw_llm_response="fallback",
    )


def decompose(intent: IntentResult, max_steps: int = 5) -> PlanV1:
    """Decompose task menjadi chain langkah-langkah agent."""
    graph = load_default_graph()
    from el_solver.llm import call_claude_cli

    prompt = (
        f"Task: {intent.raw_message}\n"
        f"Available agents+capabilities:\n{graph.describe_capabilities()}\n\n"
        f"Decompose max {max_steps} steps, tiap step assign 1 agent. "
        "JSON output: {steps:[{step_id,description,agent_assignee,depends_on:[]}], reasoning}"
    )

    try:
        raw, *_ = call_claude_cli(prompt, timeout=60)
        draft = _parse_decomposition(raw)
        steps = draft.steps[:max_steps]
        if not steps:
            raise PlanError("Decomposer tidak menghasilkan steps")
        _validate_dependencies(steps)
        _validate_agent_assignees(steps, graph)
        return _build_plan_from_steps(intent, steps, graph, raw_llm_response=raw, reasoning=draft.reasoning)
    except Exception as exc:
        logger.warning(f"decomposer: fallback ke heuristic plan: {exc}")
        return _fallback_plan(intent, graph)
