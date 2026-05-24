"""
Memory engine 2-tier untuk EL SOLVER.

Tier "always":
  File yang selalu di-load tiap turn → dimasukkan ke system prompt.
  Default: memory/MEMORY.md, memory/user/profile.md, memory/user/preferences.md.
  Total di-cap ~2KB; kalau melebihi, log warning.

Tier "on-demand":
  Semua file lain di memory/. Diakses LLM via tool call `memory_search(query)`.
  MVP pakai grep + filename match (cukup untuk <500 file).
  Tidak pakai embedding di MVP.

Semua write dijaga FileLock + dicatat di audit log.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from el_solver.config import settings
from el_solver.utils.locks import append_audit, memory_lock
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)


# File-file yang dianggap "always-loaded". Relatif terhadap memory_dir.
ALWAYS_LOADED_PATHS = (
    "MEMORY.md",
    "user/profile.md",
    "user/preferences.md",
)

# Hard cap untuk total core memory (warning, bukan error)
CORE_SOFT_CAP_BYTES = 2 * 1024  # 2 KB
CORE_HARD_CAP_BYTES = 8 * 1024  # 8 KB → di atas ini WARN keras


@dataclass
class MemoryEntry:
    """1 file memory + metadata-nya."""
    path: Path
    relative_path: str  # relatif terhadap memory_dir, mis. "projects/tour-bali.md"
    name: str  # dari frontmatter, fallback ke filename
    description: str
    type: str
    body: str
    score: float = 0.0  # untuk hasil search

    def to_brief(self) -> str:
        """1-baris ringkas, untuk hasil search."""
        return f"- {self.relative_path} — {self.description or '(no description)'}"


# ============================================================
# Tier "always" — selalu di-load
# ============================================================

def load_core() -> str:
    """
    Concat file-file tier "always". Return string siap di-append ke system prompt.
    """
    memory_dir = settings.memory_path
    parts: list[str] = []
    total_bytes = 0

    for rel in ALWAYS_LOADED_PATHS:
        p = memory_dir / rel
        if not p.exists():
            logger.debug(f"core memory missing: {rel}")
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"gagal baca core memory {rel}: {e}")
            continue
        parts.append(f"### {rel}\n{text.strip()}")
        total_bytes += len(text.encode("utf-8"))

    if total_bytes > CORE_HARD_CAP_BYTES:
        logger.warning(
            f"Core memory besar ({total_bytes}B > {CORE_HARD_CAP_BYTES}B hard cap). "
            "Pertimbangkan konsolidasi."
        )
    elif total_bytes > CORE_SOFT_CAP_BYTES:
        logger.info(
            f"Core memory mendekati cap ({total_bytes}B / {CORE_SOFT_CAP_BYTES}B soft)."
        )

    return "\n\n".join(parts) if parts else "(memory inti kosong)"


# ============================================================
# Tier "on-demand" — search & CRUD
# ============================================================

def _read_entry(path: Path) -> MemoryEntry | None:
    """Baca 1 file memory dengan frontmatter."""
    try:
        post = frontmatter.load(str(path))
    except Exception as e:
        logger.warning(f"gagal parse frontmatter {path}: {e}")
        return None
    rel = str(path.relative_to(settings.memory_path))
    fm = post.metadata or {}
    return MemoryEntry(
        path=path,
        relative_path=rel,
        name=fm.get("name") or path.stem,
        description=fm.get("description") or "",
        type=fm.get("type") or "note",
        body=post.content or "",
    )


def list_all(exclude_always: bool = True) -> list[MemoryEntry]:
    """List semua file .md di memory/."""
    memory_dir = settings.memory_path
    always_set = {str(memory_dir / rel) for rel in ALWAYS_LOADED_PATHS}
    entries: list[MemoryEntry] = []
    for path in memory_dir.rglob("*.md"):
        if exclude_always and str(path) in always_set:
            continue
        entry = _read_entry(path)
        if entry:
            entries.append(entry)
    return entries


def search(query: str, top_k: int = 5) -> list[MemoryEntry]:
    """
    Search ringan: skor berdasarkan match di filename, name, description, dan body.
    Tidak pakai embedding — cukup untuk <500 file.

    Skor:
      +5 kalau query muncul di filename / name
      +3 kalau muncul di description
      +1 per match di body (max 10)
    """
    query_low = query.lower().strip()
    if not query_low:
        return []
    tokens = [t for t in re.split(r"\s+", query_low) if t]
    if not tokens:
        return []

    candidates: list[MemoryEntry] = []
    for entry in list_all(exclude_always=True):
        score = 0.0
        hay_name = (entry.relative_path + " " + entry.name).lower()
        hay_desc = entry.description.lower()
        hay_body = entry.body.lower()
        for tok in tokens:
            if tok in hay_name:
                score += 5
            if tok in hay_desc:
                score += 3
            body_matches = min(hay_body.count(tok), 10)
            score += body_matches
        if score > 0:
            entry.score = score
            candidates.append(entry)

    candidates.sort(key=lambda e: e.score, reverse=True)
    return candidates[:top_k]


# ============================================================
# Write API
# ============================================================

VALID_CATEGORIES = {"projects", "notes", "tasks", "user"}


def _slugify(text: str) -> str:
    """Bikin nama file aman: lower, dash, alphanumeric."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "untitled"


def save(
    category: str,
    name: str,
    description: str,
    body: str,
    type_: str = "note",
    actor: str = "agent",
    overwrite: bool = False,
) -> MemoryEntry:
    """
    Simpan file memory baru.

    category: "projects" | "notes" | "tasks" | "user"
    name: judul human-readable. Filename = slugify(name).md
    description: 1-baris untuk index & search
    body: konten markdown
    type_: untuk frontmatter
    actor: untuk audit log (mis. "cli", "telegram", "wildan-manual")
    overwrite: kalau True, replace file existing
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"category harus salah satu {VALID_CATEGORIES}, got '{category}'"
        )
    settings.ensure_dirs()

    filename = _slugify(name) + ".md"
    target = settings.memory_path / category / filename
    rel = str(target.relative_to(settings.memory_path))

    with memory_lock(target):
        existed = target.exists()
        if existed and not overwrite:
            raise FileExistsError(
                f"Memory file sudah ada: {rel}. "
                "Pakai update() atau set overwrite=True."
            )

        post = frontmatter.Post(
            body,
            **{"name": name, "description": description, "type": type_},
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(frontmatter.dumps(post), encoding="utf-8")

    append_audit(
        action="update" if existed else "create",
        path=rel,
        actor=actor,
        extra={"name": name, "type": type_, "bytes": len(body)},
    )
    logger.info(f"memory {'updated' if existed else 'created'}: {rel}")

    entry = _read_entry(target)
    assert entry is not None
    return entry


def update(relative_path: str, body: str, actor: str = "agent") -> MemoryEntry:
    """Update body file existing. Frontmatter dipertahankan."""
    target = settings.memory_path / relative_path
    if not target.exists():
        raise FileNotFoundError(f"Memory file tidak ada: {relative_path}")

    with memory_lock(target):
        post = frontmatter.load(str(target))
        post.content = body
        target.write_text(frontmatter.dumps(post), encoding="utf-8")

    append_audit(
        action="update", path=relative_path, actor=actor, extra={"bytes": len(body)}
    )
    logger.info(f"memory updated: {relative_path}")

    entry = _read_entry(target)
    assert entry is not None
    return entry


def delete(relative_path: str, actor: str = "agent") -> None:
    """Hapus file memory."""
    target = settings.memory_path / relative_path
    if not target.exists():
        raise FileNotFoundError(f"Memory file tidak ada: {relative_path}")
    # Lindungi file core
    if relative_path in ALWAYS_LOADED_PATHS:
        raise PermissionError(f"Tidak boleh hapus file core: {relative_path}")

    with memory_lock(target):
        target.unlink()

    append_audit(action="delete", path=relative_path, actor=actor)
    logger.info(f"memory deleted: {relative_path}")


def get(relative_path: str) -> MemoryEntry | None:
    """Baca 1 file memory by relative path."""
    target = settings.memory_path / relative_path
    if not target.exists():
        return None
    return _read_entry(target)
