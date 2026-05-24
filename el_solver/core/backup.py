"""Backup utility — zip data/, agents/, memory/ ke ~/Documents/El Solver Backup/."""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).parent.parent.parent


def backup_now(dest_dir: Path | None = None) -> Path:
    """Buat backup ZIP. Return path ZIP yang dibuat."""
    if dest_dir is None:
        dest_dir = Path.home() / "Documents" / "El Solver Backup"
    dest_dir = dest_dir.expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = dest_dir / f"el-solver-backup-{ts}.zip"

    dirs_to_backup = ["data", "agents", "memory"]
    _SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".mypy_cache", ".ruff_cache"}
    _SKIP_SUFFIXES = {".pyc", ".pyo"}

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirname in dirs_to_backup:
            src = _ROOT / dirname
            if not src.exists():
                continue
            for file in src.rglob("*"):
                if not file.is_file():
                    continue
                if any(part in _SKIP_DIRS for part in file.parts):
                    continue
                if file.suffix in _SKIP_SUFFIXES:
                    continue
                arcname = file.relative_to(_ROOT)
                zf.write(file, arcname)

    size_mb = zip_path.stat().st_size / 1_048_576
    logger.info(f"backup_now: {zip_path.name} ({size_mb:.1f} MB)")
    return zip_path


def get_backup_info() -> dict:
    """Return info tentang folder backup dan file terbaru."""
    backup_dir = Path.home() / "Documents" / "El Solver Backup"
    if not backup_dir.exists():
        return {"last_backup": None, "backup_count": 0, "backup_dir": str(backup_dir)}

    backups = sorted(backup_dir.glob("el-solver-backup-*.zip"), reverse=True)
    last = backups[0] if backups else None
    return {
        "last_backup": last.name if last else None,
        "last_backup_path": str(last) if last else None,
        "backup_count": len(backups),
        "backup_dir": str(backup_dir),
    }
