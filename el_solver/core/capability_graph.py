"""Capability graph built from skills.json + registered skills + agents_registry.capabilities."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from el_solver.config import PROJECT_ROOT
from el_solver.skills import discover_registered
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_SKILLS_PATH = PROJECT_ROOT / "el_solver" / "web" / "library" / "skills.json"


@dataclass
class SkillNode:
    skill_id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    provided_by_agents: list[str] = field(default_factory=list)
    registered: bool = False


def _parse_json_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, str):
        return [parsed.strip()] if parsed.strip() else []
    return []


def _load_skills_file(skills_path: Path) -> list[dict]:
    if not skills_path.exists():
        return []
    try:
        data = json.loads(skills_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning(f"capability_graph: gagal baca {skills_path}: {exc}")
        return []


def _load_agent_capabilities(db_path: Path | None = None) -> dict[str, list[str]]:
    conn = get_connection(db_path)
    try:
        try:
            rows = conn.execute(
                "SELECT name, capabilities, skills, status, created_at FROM agents_registry"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT name, skills, status, created_at FROM agents_registry"
            ).fetchall()
            return {row["name"]: _parse_json_list(row["skills"]) for row in rows}

        capability_map: dict[str, list[str]] = {}
        for row in rows:
            capabilities = row["capabilities"] if "capabilities" in row.keys() else None
            raw = capabilities or row["skills"]
            capability_map[row["name"]] = _parse_json_list(raw)
        return capability_map
    finally:
        conn.close()


def _skill_meta_from_callable(func: object, skill_id: str) -> dict[str, object]:
    meta = getattr(func, "__skill_meta__", {}) or {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "id": skill_id,
        "name": str(meta.get("name") or skill_id),
        "description": str(meta.get("description") or ""),
        "requires_skills": _parse_json_list(meta.get("requires_skills")),
        "inputs": _parse_json_list(meta.get("inputs")),
        "outputs": _parse_json_list(meta.get("outputs")),
        "side_effects": _parse_json_list(meta.get("side_effects")),
        "risk": int(meta.get("risk") or 0),
    }


class CapabilityGraph:
    """Graph skill yang menghubungkan skill prerequisites dan agent provider."""

    def __init__(
        self,
        skills_path: Path | None = None,
        db_path: Path | None = None,
    ) -> None:
        self.skills_path = skills_path or _SKILLS_PATH
        self.db_path = db_path
        self.nodes: dict[str, SkillNode] = {}
        self._load()

    def _load(self) -> None:
        skills_data = _load_skills_file(self.skills_path)
        registered = discover_registered()
        agent_caps = _load_agent_capabilities(self.db_path)
        for entry in skills_data:
            skill_id = str(entry.get("id") or "").strip()
            if not skill_id:
                continue
            node = SkillNode(
                skill_id=skill_id,
                name=str(entry.get("name") or skill_id),
                description=str(entry.get("description") or ""),
                tags=[str(tag).strip() for tag in entry.get("tags", []) if str(tag).strip()],
                requires=_parse_json_list(entry.get("requires_skills")),
                provided_by_agents=[],
                registered=False,
            )
            self.nodes[skill_id] = node

        for skill_id, func in registered.items():
            meta = _skill_meta_from_callable(func, skill_id)
            node = self.nodes.get(skill_id)
            if node is None:
                node = SkillNode(
                    skill_id=skill_id,
                    name=str(meta["name"]),
                    description=str(meta["description"]),
                    tags=[],
                    requires=list(meta["requires_skills"]),
                    provided_by_agents=[],
                    registered=True,
                )
                self.nodes[skill_id] = node
            else:
                node.registered = True
                if not node.name or node.name == skill_id:
                    node.name = str(meta["name"])
                if not node.description:
                    node.description = str(meta["description"])
                merged_requires = list(dict.fromkeys(node.requires + list(meta["requires_skills"])))
                node.requires = merged_requires

        for agent_name, capabilities in agent_caps.items():
            for skill_id in capabilities:
                if skill_id in self.nodes:
                    self.nodes[skill_id].provided_by_agents.append(agent_name)

        for node in self.nodes.values():
            node.provided_by_agents = sorted(dict.fromkeys(node.provided_by_agents))

    def skill_ids(self) -> list[str]:
        return list(self.nodes.keys())

    def find_agent_for_skill(self, skill_id: str) -> str | None:
        node = self.nodes.get(skill_id)
        if not node or not node.provided_by_agents:
            return None
        return node.provided_by_agents[0]

    def resolve_skill_chain(self, target: str) -> list[str]:
        if target not in self.nodes:
            raise KeyError(f"skill tidak ditemukan: {target}")

        resolved: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(skill_id: str) -> None:
            if skill_id in visiting:
                raise ValueError(f"cycle detected in skill graph at {skill_id}")
            if skill_id in visited:
                return
            if skill_id not in self.nodes:
                raise KeyError(f"skill tidak ditemukan: {skill_id}")

            visiting.add(skill_id)
            for parent in self.nodes[skill_id].requires:
                dfs(parent)
            visiting.remove(skill_id)
            visited.add(skill_id)
            resolved.append(skill_id)

        dfs(target)
        return resolved

    def gap_for_task(self, keywords: Iterable[str]) -> list[str]:
        keyword_list = [str(keyword).lower().strip() for keyword in keywords if str(keyword).strip()]
        if not keyword_list:
            return []

        matched: list[str] = []
        for skill_id, node in self.nodes.items():
            haystack = " ".join([skill_id, node.name, node.description, " ".join(node.tags)]).lower()
            if any(keyword in haystack for keyword in keyword_list):
                try:
                    chain = self.resolve_skill_chain(skill_id)
                except ValueError:
                    chain = [skill_id]
                for item in chain:
                    if item not in matched:
                        matched.append(item)

        return [skill_id for skill_id in matched if not self.find_agent_for_skill(skill_id)]

    def describe_agents(self) -> str:
        return self.describe_capabilities()

    def describe_capabilities(self) -> str:
        lines: list[str] = []
        agent_to_caps: dict[str, list[str]] = {}
        for node in self.nodes.values():
            for agent_name in node.provided_by_agents:
                agent_to_caps.setdefault(agent_name, []).append(node.skill_id)

        for agent_name in sorted(agent_to_caps):
            caps = ", ".join(sorted(dict.fromkeys(agent_to_caps[agent_name]))) or "-"
            lines.append(f"- {agent_name}: {caps}")
        registered_only = [node for node in self.nodes.values() if node.registered]
        if registered_only:
            lines.append("Registered skills:")
            for node in sorted(registered_only, key=lambda n: n.skill_id):
                requires = f" | requires: {', '.join(node.requires)}" if node.requires else ""
                lines.append(f"- {node.skill_id}: {node.name}{requires}")
        return "\n".join(lines) if lines else "- (belum ada agent capability)"


    def attach_agents(self, agent_map: dict[str, list[str]]) -> None:
        """Populate the graph from agent manifests (R13 M4).

        Additive only: existing nodes gain providers; capabilities with no
        skills.json/registered node get a lightweight node created so they
        are still addressable via ``find_agent_for_skill``. Default
        ``_load`` behaviour is unchanged.
        """
        for agent_name, capabilities in agent_map.items():
            for skill_id in capabilities:
                node = self.nodes.get(skill_id)
                if node is None:
                    node = SkillNode(
                        skill_id=skill_id,
                        name=skill_id,
                        description="",
                        tags=[],
                        requires=[],
                        provided_by_agents=[],
                        registered=False,
                    )
                    self.nodes[skill_id] = node
                if agent_name not in node.provided_by_agents:
                    node.provided_by_agents.append(agent_name)
        for node in self.nodes.values():
            node.provided_by_agents = sorted(dict.fromkeys(node.provided_by_agents))


@lru_cache(maxsize=1)
def load_default_graph() -> CapabilityGraph:
    return CapabilityGraph()


def load_graph_with_agents(agents_dir: Path | None = None) -> CapabilityGraph:
    """Capability graph with agent manifests attached (R13 M4)."""
    from el_solver.agents.base import agent_capability_map

    graph = CapabilityGraph()
    base_dir = agents_dir or (PROJECT_ROOT / "agents")
    graph.attach_agents(agent_capability_map(base_dir))
    return graph
