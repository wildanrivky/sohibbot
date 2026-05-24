"""Discovery helper untuk skill registered yang di-merge human-approved."""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
from pathlib import Path
from typing import Any, Callable

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_REGISTERED_DIR = PROJECT_ROOT / "el_solver" / "skills" / "registered"


def _load_module(path: Path) -> object | None:
    safe_stem = re.sub(r"[^a-zA-Z0-9_]", "_", path.stem)
    module_name = f"el_solver.skills.registered.{safe_stem}_{path.stat().st_mtime_ns}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _skill_id_from_path(path: Path) -> str:
    stem = path.stem
    return stem[6:] if stem.startswith("skill_") else stem


def discover_registered() -> dict[str, Callable[..., Any]]:
    """Scan skills/registered/skill_*.py dan return skill_id -> callable."""
    discovered: dict[str, Callable[..., Any]] = {}
    if not _REGISTERED_DIR.exists():
        return discovered

    for path in sorted(_REGISTERED_DIR.glob("skill_*.py")):
        try:
            module = _load_module(path)
            if module is None:
                continue
            skill_id = _skill_id_from_path(path)
            callable_obj: Callable[..., Any] | None = None

            for _, obj in inspect.getmembers(module, inspect.isfunction):
                meta = getattr(obj, "__skill_meta__", None)
                if not meta:
                    continue
                try:
                    from el_solver.skills.decorator import validate_skill_metadata

                    validate_skill_metadata(meta)
                except Exception as exc:
                    logger.warning(f"discover_registered: metadata invalid di {path.name}: {exc}")
                    continue
                callable_obj = obj
                break

            if callable_obj is None:
                logger.warning(f"discover_registered: tidak ada @skill decorated function di {path.name}")
                continue

            discovered[skill_id] = callable_obj
        except Exception as exc:
            logger.warning(f"discover_registered: gagal load {path.name}: {exc}")

    return discovered


def get_skill(skill_id: str) -> Callable[..., Any] | None:
    """Ambil callable skill registered jika tersedia."""
    return discover_registered().get(skill_id)
