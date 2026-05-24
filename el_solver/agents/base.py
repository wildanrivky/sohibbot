"""Shared Agent base class for the El Solver agent mesh (R13 M1).

Before R13 every agent in ``agents/*/`` was an isolated subprocess script:
``run.py`` shelled out to the Claude CLI and printed text. Nothing connected
those scripts to ``el_solver.core`` (registry, capability graph, tracer).

This module gives every agent one shared spine:

  - ``load_manifest`` / ``manifest_capabilities`` — read ``manifest.yaml``.
  - ``Agent`` — wraps an agent's existing ``run(message) -> str`` callable and
    provides the three mesh primitives from the blueprint (Section 6.3):
    ``receive_task`` (acceptance check), ``execute`` (traced run),
    ``report_result`` (structured roll-up).
  - ``Agent.cli_main`` — drop-in replacement for the old ``main()`` so a
    migrated ``run.py`` keeps the exact stdout/exit contract that
    ``el_solver.core.agent_io.invoke_subagent`` depends on.

The runtime contract is deliberately unchanged: ``cli_main`` still prints the
agent's text to stdout and exits non-zero on failure. The new behaviour is
additive — a best-effort trace is written so telemetry stays alive.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

Runner = Callable[[str], str]


# ── Manifest helpers ──────────────────────────────────────────────────────────

def load_manifest(agent_dir: Path) -> dict[str, Any]:
    """Parse ``manifest.yaml`` in ``agent_dir``. Return ``{}`` if missing/broken."""
    path = agent_dir / "manifest.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f"agents.base: gagal baca {path}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def manifest_capabilities(manifest: dict[str, Any]) -> list[str]:
    """Capability ids declared by a manifest.

    Resolution order:
      1. explicit ``capabilities:`` list (preferred, new field)
      2. ``trigger.keywords`` (existing reactive agents)
    De-duplicated, order preserved, blanks dropped.
    """
    raw: list[Any] = []
    explicit = manifest.get("capabilities")
    if isinstance(explicit, list):
        raw.extend(explicit)
    elif isinstance(explicit, str) and explicit.strip():
        raw.extend(part.strip() for part in explicit.split(","))

    if not raw:
        trigger = manifest.get("trigger")
        if isinstance(trigger, dict):
            kws = trigger.get("keywords")
            if isinstance(kws, list):
                raw.extend(kws)

    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


# ── Agent discovery (capability graph source, R13 M4) ─────────────────────────

@dataclass
class AgentInfo:
    """Declared identity of an agent, read straight from its manifest."""

    name: str
    archetype: str
    role: str
    status: str
    capabilities: list[str]
    path: Path

    @property
    def is_active(self) -> bool:
        return self.status in {"active", "created"}


def scan_agents(agents_dir: Path) -> list[AgentInfo]:
    """Discover every agent under ``agents_dir`` from its ``manifest.yaml``.

    A directory is an agent iff it is not ``_``-prefixed and has a manifest.
    Sorted by name for stable CLI / graph output.
    """
    out: list[AgentInfo] = []
    if not agents_dir.is_dir():
        return out
    for d in sorted(agents_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        manifest = load_manifest(d)
        if not manifest:
            continue
        out.append(
            AgentInfo(
                name=str(manifest.get("name") or d.name),
                archetype=str(manifest.get("archetype") or "conversational"),
                role=str(manifest.get("role") or ""),
                status=str(manifest.get("status") or "active"),
                capabilities=manifest_capabilities(manifest),
                path=d,
            )
        )
    return out


def agent_capability_map(agents_dir: Path) -> dict[str, list[str]]:
    """``{agent_name: [capability, ...]}`` for active agents under ``agents_dir``."""
    return {
        info.name: info.capabilities
        for info in scan_agents(agents_dir)
        if info.is_active
    }


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class AgentTask:
    """A unit of work handed to an agent (GM/Head → Worker)."""

    message: str
    task_id: str = ""
    context: str = ""
    parent_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def full_input(self) -> str:
        """Message with dependency context appended, matching chain convention."""
        if self.context.strip():
            return f"{self.message}\n\nKonteks dependensi:\n{self.context}"
        return self.message


@dataclass
class Acceptance:
    """Result of ``Agent.receive_task`` — can this agent take the task?"""

    accepted: bool
    reason: str = ""


@dataclass
class AgentResult:
    """Structured output of an agent run (Worker → Head)."""

    agent: str
    task_id: str
    status: str  # "completed" | "error"
    output: str = ""
    summary: str = ""
    confidence: float = 1.0
    basis: str = ""
    duration_ms: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "completed" and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "status": self.status,
            "output": self.output,
            "summary": self.summary,
            "confidence": self.confidence,
            "basis": self.basis,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def _short_summary(text: str, max_chars: int = 200) -> str:
    """First non-empty line, capped — same convention as agent_io."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:max_chars]
    return (text or "")[:max_chars].strip()


# ── Agent ─────────────────────────────────────────────────────────────────────

class Agent:
    """Mesh wrapper around an agent directory + its ``run(message)`` callable.

    Subclassing is optional: agents can either pass a ``runner`` callable or
    override :meth:`run`.
    """

    def __init__(
        self,
        agent_dir: Path | str,
        runner: Runner | None = None,
        *,
        trace: bool = True,
    ) -> None:
        self.agent_dir = Path(agent_dir).resolve()
        self._runner = runner
        self._trace_enabled = trace
        self.manifest = load_manifest(self.agent_dir)

    # -- declared metadata (from manifest, with safe fallbacks) ----------------

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.agent_dir.name)

    @property
    def archetype(self) -> str:
        return str(self.manifest.get("archetype") or "conversational")

    @property
    def status(self) -> str:
        return str(self.manifest.get("status") or "active")

    @property
    def risk_level(self) -> str:
        return str(self.manifest.get("risk_level") or "L0")

    @property
    def role(self) -> str:
        return str(self.manifest.get("role") or "")

    @property
    def capabilities(self) -> list[str]:
        return manifest_capabilities(self.manifest)

    def is_active(self) -> bool:
        return self.status in {"active", "created"}

    # -- mesh primitives (blueprint Section 6.3) ------------------------------

    def receive_task(self, task: AgentTask) -> Acceptance:
        """Pre-flight: is this agent able & willing to run ``task``?"""
        if not self.is_active():
            return Acceptance(False, f"agent '{self.name}' status={self.status}")
        if not task.message or not task.message.strip():
            return Acceptance(False, "task message kosong")
        return Acceptance(True, "")

    def run(self, message: str) -> str:
        """Override point. Default delegates to the injected ``runner``."""
        if self._runner is None:
            raise NotImplementedError(
                f"agent '{self.name}': no runner provided and run() not overridden"
            )
        return self._runner(message)

    def execute(self, task: AgentTask) -> AgentResult:
        """Run the task, time it, emit a best-effort trace, return a result.

        Never raises: a failed run becomes ``status='error'`` so a Head can
        replan instead of crashing the chain.
        """
        acceptance = self.receive_task(task)
        if not acceptance.accepted:
            return AgentResult(
                agent=self.name,
                task_id=task.task_id,
                status="error",
                error=acceptance.reason,
                summary=acceptance.reason,
                confidence=0.0,
                basis="receive_task rejected",
            )

        prompt = task.full_input()
        t0 = time.monotonic()
        try:
            with self._trace(prompt) as span:
                output = self.run(prompt)
                duration_ms = int((time.monotonic() - t0) * 1000)
                if span is not None:
                    span.record_output(output)
                    span.set_metadata(
                        task_id=task.task_id,
                        parent_task_id=task.parent_task_id,
                    )
            return AgentResult(
                agent=self.name,
                task_id=task.task_id,
                status="completed",
                output=output,
                summary=_short_summary(output),
                duration_ms=duration_ms,
                basis="run() completed",
            )
        except Exception as exc:  # noqa: BLE001 — surfaced as error result
            duration_ms = int((time.monotonic() - t0) * 1000)
            err = str(exc) or exc.__class__.__name__
            logger.warning(f"agent '{self.name}' execute failed: {err}")
            return AgentResult(
                agent=self.name,
                task_id=task.task_id,
                status="error",
                error=err,
                summary=_short_summary(err),
                duration_ms=duration_ms,
                confidence=0.0,
                basis="run() raised",
            )

    def report_result(self, result: AgentResult) -> dict[str, Any]:
        """Roll a result up to the caller (Head/GM). Returns a plain dict."""
        return result.to_dict()

    # -- tracing (best-effort; never breaks the run) --------------------------

    def _trace(self, prompt: str) -> Any:
        if not self._trace_enabled:
            return _NullSpanCtx()
        try:
            from el_solver.evaluations.tracer import get_tracer

            return get_tracer(self.name).trace(prompt, source="agent.base")
        except Exception as exc:  # noqa: BLE001 — telemetry must not break runs
            logger.debug(f"agent '{self.name}': tracer unavailable ({exc})")
            return _NullSpanCtx()

    # -- CLI entry point (preserves old run.py contract) ----------------------

    def cli_main(self, argv: list[str] | None = None) -> int:
        """Drop-in for the old ``main()``.

        Prints agent text to stdout on success; prints the error to stderr and
        returns exit code 1 on failure — identical observable behaviour to the
        pre-R13 scripts, so ``agent_io.invoke_subagent`` is unaffected.
        """
        args = sys.argv[1:] if argv is None else argv
        if not args:
            print("Usage: python run.py <message>", file=sys.stderr)
            return 1
        task = AgentTask(message=" ".join(args))
        result = self.execute(task)
        if result.ok:
            print(result.output)
            return 0
        print(f"Agent error: {result.error}", file=sys.stderr)
        return 1


class _NullSpanCtx:
    """Context manager that yields ``None`` — used when tracing is off."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
