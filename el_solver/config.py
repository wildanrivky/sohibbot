"""Pydantic settings — baca dari .env."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Root project el-solver/ — dipakai untuk resolve path relatif (memory/, data/)
# supaya tidak tergantung CWD saat command dijalankan.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Claude CLI ---
    claude_cli_path: str = Field(
        default="claude",
        alias="CLAUDE_CLI_PATH",
    )
    claude_model_default: str = Field(
        default="claude-sonnet-4-6",
        alias="CLAUDE_MODEL_DEFAULT",
    )

    # --- Telegram ---
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    claude_telegram_bot_token: str = Field(default="", alias="CLAUDE_TELEGRAM_BOT_TOKEN")
    telegram_owner_id: int = Field(default=0, alias="TELEGRAM_OWNER_ID")

    @property
    def active_telegram_bot_token(self) -> str:
        return self.claude_telegram_bot_token or self.telegram_bot_token

    # --- Paths ---
    memory_dir: str = Field(default="./memory", alias="MEMORY_DIR")
    data_dir: str = Field(default="./data", alias="DATA_DIR")

    # --- App ---
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Helpers ---
    def _resolve(self, raw: str) -> Path:
        """Resolve path: kalau relatif, anchor ke PROJECT_ROOT (bukan CWD)."""
        p = Path(raw)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p.resolve()

    @property
    def memory_path(self) -> Path:
        return self._resolve(self.memory_dir)

    @property
    def data_path(self) -> Path:
        return self._resolve(self.data_dir)

    @property
    def audit_log_path(self) -> Path:
        return self.data_path / "memory-audit.jsonl"

    @property
    def usage_log_path(self) -> Path:
        return self.data_path / "usage.jsonl"

    @property
    def database_path(self) -> Path:
        return self.data_path / "el-solver.db"

    def ensure_dirs(self) -> None:
        self.memory_path.mkdir(parents=True, exist_ok=True)
        (self.memory_path / "user").mkdir(parents=True, exist_ok=True)
        (self.memory_path / "projects").mkdir(parents=True, exist_ok=True)
        (self.memory_path / "notes").mkdir(parents=True, exist_ok=True)
        (self.memory_path / "tasks").mkdir(parents=True, exist_ok=True)
        self.data_path.mkdir(parents=True, exist_ok=True)
        (self.data_path / "conversations").mkdir(parents=True, exist_ok=True)


settings = Settings()
