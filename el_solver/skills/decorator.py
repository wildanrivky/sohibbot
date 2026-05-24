"""Decorator untuk skill registered yang dideploy lewat scaffolding proposal."""
from __future__ import annotations

from functools import wraps
from inspect import getdoc
from typing import Any, Callable, TypeVar

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=Callable[..., Any])


def _as_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    raise TypeError(f"{field_name} harus list[str] atau string")


def validate_skill_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    required = ("name", "risk", "inputs", "outputs", "side_effects", "requires_skills")
    for field_name in required:
        if field_name not in meta:
            raise ValueError(f"skill metadata missing field: {field_name}")

    name = str(meta["name"]).strip()
    if not name:
        raise ValueError("skill name tidak boleh kosong")

    risk = int(meta["risk"])
    if risk < 0:
        raise ValueError("skill risk harus >= 0")

    validated = {
        "name": name,
        "risk": risk,
        "inputs": _as_list(meta["inputs"], "inputs"),
        "outputs": _as_list(meta["outputs"], "outputs"),
        "side_effects": _as_list(meta["side_effects"], "side_effects"),
        "requires_skills": _as_list(meta["requires_skills"], "requires_skills"),
        "description": str(meta.get("description") or "").strip(),
    }
    return validated


def skill(
    name: str,
    risk: int,
    inputs: list[str] | tuple[str, ...] | str,
    outputs: list[str] | tuple[str, ...] | str,
    side_effects: list[str] | tuple[str, ...] | str,
    requires_skills: list[str] | tuple[str, ...] | str,
) -> Callable[[T], T]:
    """Dekorator marker untuk skill registered."""

    def decorator(func: T) -> T:
        meta = validate_skill_metadata(
            {
                "name": name,
                "risk": risk,
                "inputs": inputs,
                "outputs": outputs,
                "side_effects": side_effects,
                "requires_skills": requires_skills,
                "description": getdoc(func) or "",
            }
        )
        setattr(func, "__skill_meta__", meta)
        logger.debug(f"skill decorator: attached metadata to {func.__name__}")
        return func

    return decorator
