"""
Memory Compaction — scan memory/, temukan duplikat + file tua, hasilkan laporan.

Tidak menghapus atau merge otomatis — output hanya laporan untuk review Wildan.
Eksekusi mingguan via scripts/compact-memory.sh atau APScheduler.

Usage:
    from el_solver.memory.compaction import compact, CompactionReport
    report = compact()
    print(report.summary())
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import frontmatter

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

MEMORY_DIR = PROJECT_ROOT / "memory"
DATA_DIR = PROJECT_ROOT / "data"
REPORT_PATH = DATA_DIR / "improvement-suggestions.md"

# File yang tidak boleh disentuh
_PROTECTED = {"MEMORY.md"}
# Umur file untuk dianggap "stale" (dalam hari)
_STALE_DAYS = 180
# Minimum ukuran file untuk dicek duplikat (byte)
_MIN_SIZE_FOR_DEDUP = 50


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class MemoryFileInfo:
    path: Path
    size_bytes: int
    last_modified: datetime
    content_hash: str
    name: str = ""
    description: str = ""
    age_days: int = 0

    @property
    def is_stale(self) -> bool:
        return self.age_days >= _STALE_DAYS

    def relative_path(self) -> str:
        try:
            return str(self.path.relative_to(PROJECT_ROOT))
        except ValueError:
            return str(self.path)


@dataclass
class DuplicateGroup:
    """Sekelompok file dengan konten sangat mirip."""
    files: list[MemoryFileInfo]
    similarity: str = "exact"  # "exact" | "near"

    @property
    def primary(self) -> MemoryFileInfo:
        return self.files[0]


@dataclass
class CompactionReport:
    """Hasil analisis compaction."""
    scanned_count: int = 0
    stale_files: list[MemoryFileInfo] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    suggestions: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.stale_files or self.duplicate_groups)

    def summary(self) -> str:
        lines = [
            f"# Memory Compaction Report",
            f"Generated: {self.generated_at}",
            f"Files scanned: {self.scanned_count}",
            "",
        ]

        if self.stale_files:
            lines.append(f"## Stale Files ({len(self.stale_files)} — tidak diakses > {_STALE_DAYS} hari)")
            for f in self.stale_files:
                lines.append(f"- `{f.relative_path()}` ({f.age_days} hari, {f.size_bytes}B)")
            lines.append("")

        if self.duplicate_groups:
            lines.append(f"## Possible Duplicates ({len(self.duplicate_groups)} grup)")
            for g in self.duplicate_groups:
                lines.append(f"- **{g.similarity}** match:")
                for f in g.files:
                    lines.append(f"  - `{f.relative_path()}`")
            lines.append("")

        if self.suggestions:
            lines.append("## Saran")
            for s in self.suggestions:
                lines.append(f"- {s}")
            lines.append("")

        if not self.has_issues:
            lines.append("✅ Tidak ada isu ditemukan. Memory terlihat bersih.")

        return "\n".join(lines)

    def telegram_summary(self) -> str:
        """Versi ringkas untuk dikirim ke Telegram."""
        parts = [f"🗂 Memory Compaction Report ({self.generated_at[:10]})"]
        parts.append(f"Files: {self.scanned_count}")
        if self.stale_files:
            parts.append(f"⏰ Stale: {len(self.stale_files)} files (>{_STALE_DAYS}d)")
        if self.duplicate_groups:
            parts.append(f"🔁 Duplikat: {len(self.duplicate_groups)} grup")
        if not self.has_issues:
            parts.append("✅ Memory bersih")
        parts.append(f"Detail: data/improvement-suggestions.md")
        return "\n".join(parts)


# ── Scanner ────────────────────────────────────────────────────────────────────

def _file_hash(path: Path) -> str:
    """MD5 hash konten file."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def _age_days(path: Path) -> int:
    """Hari sejak file terakhir dimodifikasi."""
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).days


def _read_frontmatter(path: Path) -> tuple[str, str]:
    """Return (name, description) dari frontmatter, atau ("", "")."""
    try:
        post = frontmatter.load(str(path))
        return (
            str(post.metadata.get("name", "")),
            str(post.metadata.get("description", "")),
        )
    except Exception:
        return "", ""


def scan_memory_files(memory_dir: Path | None = None) -> list[MemoryFileInfo]:
    """Scan semua .md files di memory_dir, return list MemoryFileInfo."""
    base = memory_dir or MEMORY_DIR
    if not base.exists():
        return []

    result: list[MemoryFileInfo] = []
    for path in sorted(base.rglob("*.md")):
        if path.name in _PROTECTED:
            continue
        if path.stat().st_size == 0:
            continue
        name, desc = _read_frontmatter(path)
        age = _age_days(path)
        info = MemoryFileInfo(
            path=path,
            size_bytes=path.stat().st_size,
            last_modified=datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ),
            content_hash=_file_hash(path),
            name=name,
            description=desc,
            age_days=age,
        )
        result.append(info)
    return result


def find_stale_files(files: list[MemoryFileInfo]) -> list[MemoryFileInfo]:
    """Return file yang tidak dimodifikasi > STALE_DAYS."""
    return [f for f in files if f.is_stale]


def find_duplicates(files: list[MemoryFileInfo]) -> list[DuplicateGroup]:
    """
    Temukan file dengan konten identik (exact hash match).
    Near-duplicate (semantic) defer ke Phase 4 saat vector DB ada.
    """
    if len(files) < 2:
        return []

    # Group by content hash
    by_hash: dict[str, list[MemoryFileInfo]] = {}
    for f in files:
        if f.size_bytes < _MIN_SIZE_FOR_DEDUP:
            continue
        by_hash.setdefault(f.content_hash, []).append(f)

    groups: list[DuplicateGroup] = []
    for hash_val, group in by_hash.items():
        if len(group) > 1:
            groups.append(DuplicateGroup(files=group, similarity="exact"))

    return groups


def _generate_suggestions(
    stale: list[MemoryFileInfo],
    dupes: list[DuplicateGroup],
) -> list[str]:
    suggestions: list[str] = []
    if stale:
        suggestions.append(
            f"Pertimbangkan archive {len(stale)} file stale "
            f"(tidak diakses > {_STALE_DAYS} hari) ke memory/archive/."
        )
    if dupes:
        total_dupes = sum(len(g.files) - 1 for g in dupes)
        suggestions.append(
            f"Ada {total_dupes} file duplikat yang bisa dihapus (konten identik)."
        )
    if not suggestions:
        suggestions.append("Memory terlihat bersih. Tidak ada aksi yang dibutuhkan.")
    return suggestions


# ── Main compact() ─────────────────────────────────────────────────────────────

def compact(
    memory_dir: Path | None = None,
    output_path: Path | None = None,
    dry_run: bool = True,
) -> CompactionReport:
    """
    Scan memory/, generate compaction report, simpan ke data/improvement-suggestions.md.

    Args:
        memory_dir  : override direktori memory (default: PROJECT_ROOT/memory/)
        output_path : override output path (default: DATA_DIR/improvement-suggestions.md)
        dry_run     : kalau True, tidak merge/delete — hanya report (selalu True di Phase 2)

    Returns:
        CompactionReport
    """
    base = memory_dir or MEMORY_DIR
    out_path = output_path or REPORT_PATH

    logger.info(f"compaction: scanning {base}...")
    files = scan_memory_files(base)
    logger.info(f"compaction: {len(files)} files found")

    stale = find_stale_files(files)
    dupes = find_duplicates(files)
    suggestions = _generate_suggestions(stale, dupes)

    report = CompactionReport(
        scanned_count=len(files),
        stale_files=stale,
        duplicate_groups=dupes,
        suggestions=suggestions,
    )

    # Persist report
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.summary(), encoding="utf-8")
    logger.info(f"compaction: report written to {out_path}")

    if stale:
        logger.warning(f"compaction: {len(stale)} stale files found")
    if dupes:
        logger.warning(f"compaction: {len(dupes)} duplicate groups found")

    return report


if __name__ == "__main__":
    r = compact()
    print(r.summary())
