"""Agent — thin wrapper di atas claude CLI subprocess."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from el_solver.config import settings
from el_solver.llm import call_claude_cli
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_versioned_prompt(agent_name: str) -> str | None:
    """Load prompt file versi terbaru untuk agent. Return None kalau tidak ada."""
    if not _PROMPTS_DIR.exists():
        return None
    pattern = re.compile(rf"^{re.escape(agent_name)}-v(\d+)\.md$")
    candidates: list[tuple[int, Path]] = []
    for f in _PROMPTS_DIR.iterdir():
        m = pattern.match(f.name)
        if m:
            candidates.append((int(m.group(1)), f))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    latest_version, latest_path = candidates[0]
    try:
        content = latest_path.read_text(encoding="utf-8").strip()
        logger.info(f"agent: loaded versioned prompt {latest_path.name}")
        return content
    except Exception as exc:
        # Fallback ke versi sebelumnya
        if len(candidates) > 1:
            try:
                content = candidates[1][1].read_text(encoding="utf-8").strip()
                logger.warning(f"agent: fallback ke {candidates[1][1].name} karena {exc}")
                return content
            except Exception:
                pass
        logger.error(f"agent: gagal load versioned prompt: {exc}")
        return None


class BudgetExceeded(RuntimeError):
    """Dilempar kalau token harian habis sebelum invoke."""


@dataclass
class AgentTurn:
    text: str
    duration: float
    tokens_used: int = 0
    session_id: str | None = None  # Claude CLI session_id untuk --resume berikutnya


class Agent:
    def __init__(self, model: str | None = None, agent_name: str | None = None) -> None:
        self.model = model
        self.agent_name = agent_name

    def run(
        self,
        user_message: str,
        timeout: int = 120,
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> AgentTurn:
        settings.ensure_dirs()

        if self.agent_name:
            try:
                from el_solver.core.scheduler import check_budget, record_tokens
                ok, reason = check_budget(self.agent_name)
                if not ok:
                    logger.warning(f"agent.run: budget exceeded untuk '{self.agent_name}': {reason}")
                    raise BudgetExceeded(reason)
            except ImportError:
                pass

        # Inject versioned prompt rules kalau ada
        versioned_prompt = _load_versioned_prompt(self.agent_name) if self.agent_name else None
        if versioned_prompt:
            user_message = f"{versioned_prompt}\n\n---\n\n{user_message}"

        logger.info(f"agent.run: {len(user_message)} chars conv_id={conversation_id!r}")
        text, duration, session_id = call_claude_cli(
            user_message,
            model=self.model,
            timeout=timeout,
            conversation_id=conversation_id,
            run_id=run_id,
        )

        # Gunakan actual token count dari JSON output kalau tersedia
        tokens_used = max(len(user_message) // 4 + len(text) // 4, 1)

        if self.agent_name:
            try:
                from el_solver.core.scheduler import record_tokens
                record_tokens(self.agent_name, tokens_used)
            except ImportError:
                pass

        return AgentTurn(text=text, duration=duration, tokens_used=tokens_used, session_id=session_id)
