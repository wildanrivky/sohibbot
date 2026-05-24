"""
Agent I/O — lightweight wrapper untuk subprocess invocation ke sub-agent.

Probe order entry point:
  1. run.py           — standard agents
  2. create_carousel.py — carousel agents (carousel-account1, carousel-account2)
  3. First *.py dengan __main__ guard — fallback
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentResult:
    text: str
    summary: str
    exit_code: int = 0
    duration_ms: int = 0
    entry_point: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


def _probe_entry_point(agent_dir: Path) -> tuple[str, list[str]] | None:
    """
    Return (mode, cmd_suffix) kalau entry point ditemukan, else None.
    mode: "run" | "carousel" | "generic"
    cmd_suffix: argumen setelah python bin (tanpa prompt — caller inject)
    """
    run_py = agent_dir / "run.py"
    carousel_py = agent_dir / "create_carousel.py"
    thumbnail_py = agent_dir / "create_thumbnail.py"

    if run_py.exists():
        return "run", [str(run_py)]
    if carousel_py.exists():
        return "carousel", [str(carousel_py)]
    if thumbnail_py.exists():
        return "thumbnail", [str(thumbnail_py)]

    # Fallback: cari *.py pertama dengan `if __name__` guard
    for py_file in sorted(agent_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if '__name__' in content and '__main__' in content:
                return "generic", [str(py_file)]
        except OSError:
            continue
    return None


def _short_summary(text: str, max_chars: int = 200) -> str:
    """First non-empty line, capped at max_chars."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:max_chars]
    return text[:max_chars].strip()


def invoke_subagent(
    agent_dir: Path,
    prompt: str,
    timeout: int = 120,
    extra_args: list[str] | None = None,
    env: dict | None = None,
) -> AgentResult:
    """
    Probe entry point di agent_dir, spawn subprocess, return AgentResult.

    Untuk carousel agents (entry point = create_carousel.py), prompt dipakai
    sebagai nilai --idea. Untuk run.py / generic, prompt dipass sebagai arg posisional.
    """
    probe = _probe_entry_point(agent_dir)
    if probe is None:
        err = f"Tidak ada entry point di {agent_dir}"
        return AgentResult(text=err, summary=err, exit_code=1, error=err)

    mode, cmd_suffix = probe

    if mode == "carousel":
        cmd = [sys.executable] + cmd_suffix + ["--idea", prompt]
    elif mode == "thumbnail":
        cmd = [sys.executable] + cmd_suffix + ["--topic", prompt]
    else:
        cmd = [sys.executable] + cmd_suffix + [prompt]

    if extra_args:
        cmd.extend(extra_args)

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(agent_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        raw_out = proc.stdout.strip() or proc.stderr.strip() or "(tidak ada output)"
        error = None if proc.returncode == 0 else f"exit {proc.returncode}"
        return AgentResult(
            text=raw_out,
            summary=_short_summary(raw_out),
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            entry_point=cmd_suffix[0] if cmd_suffix else "",
            error=error,
        )
    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - t0) * 1000)
        err = f"Timeout setelah {timeout}s"
        return AgentResult(
            text=err, summary=err, exit_code=-1, duration_ms=duration_ms,
            entry_point=cmd_suffix[0] if cmd_suffix else "", error=err,
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        err = str(exc)
        return AgentResult(
            text=err, summary=err, exit_code=-1, duration_ms=duration_ms,
            entry_point=cmd_suffix[0] if cmd_suffix else "", error=err,
        )


def list_available_agents(agents_dir: Path) -> list[str]:
    """List agent names yang punya entry point valid di agents_dir."""
    results: list[str] = []
    if not agents_dir.is_dir():
        return results
    for d in sorted(agents_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if _probe_entry_point(d) is not None:
            results.append(d.name)
    return results
