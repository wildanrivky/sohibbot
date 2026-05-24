"""File lock + audit log untuk memory writes."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock

from el_solver.config import settings


@contextmanager
def memory_lock(path: Path | str, timeout: float = 10.0) -> Iterator[None]:
    """
    Context manager: kunci file di memory/ supaya tidak race
    antara channel (CLI vs Telegram bot vs Claude CLI editing manual).

    Lock file disimpan di samping file aslinya: foo.md.lock
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock_path = p.with_suffix(p.suffix + ".lock")
    with FileLock(str(lock_path), timeout=timeout):
        yield


def append_audit(
    action: str,
    path: str,
    actor: str = "agent",
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Append 1 baris JSON ke data/memory-audit.jsonl.
    Catat siapa/kapan/file/aksi → bantu debug saat memory kacau.
    """
    settings.data_path.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "action": action,  # "create" | "update" | "delete" | "read"
        "path": str(path),
    }
    if extra:
        record["extra"] = extra
    with open(settings.audit_log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
