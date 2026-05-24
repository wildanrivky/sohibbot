"""Agent mesh layer (R13).

Shared base class + manifest loading so the standalone `agents/*/run.py`
scripts become load-bearing nodes in the El Solver agent graph instead of
isolated subprocess scripts.
"""
from __future__ import annotations

from el_solver.agents.base import (
    Acceptance,
    Agent,
    AgentInfo,
    AgentResult,
    AgentTask,
    agent_capability_map,
    load_manifest,
    manifest_capabilities,
    scan_agents,
)

__all__ = [
    "Acceptance",
    "Agent",
    "AgentInfo",
    "AgentResult",
    "AgentTask",
    "agent_capability_map",
    "load_manifest",
    "manifest_capabilities",
    "scan_agents",
]
