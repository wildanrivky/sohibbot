"""Hybrid memory retrieval + Tier-1 goal-state (R14 M4).

Two design constraints shaped this module:

1. **No model download.** The blueprint suggests bge-small/fastembed, but
   pulling a model overnight is fragile. This ships a dependency-free,
   *deterministic* local embedder (hashed bag-of-tokens → fixed-dim
   L2-normalized vector + cosine). `set_embedder()` lets a later round
   swap in fastembed without touching callers. Grep stays the strong,
   deterministic signal — results are always *hybrid*, never vector-only.

2. **Deliberate decoupling from `el_solver.memory`.** This module reads the
   `memory/` directory directly via `settings.memory_path` rather than
   importing the memory package, keeping retrieval deterministic and free of
   that package's import surface. (The historical package-shadow bug where
   `memory.load_core`/`search` raised AttributeError is now fixed.)
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import frontmatter  # type: ignore[import-untyped]

from el_solver.config import settings
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_DIM = 256
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_INDEX_FILE = "MEMORY.md"

# Tier-1 "goal-state" memory (blueprint 5.3)
TIER1_PATHS = (
    "business/portfolio.md",
    "business/active-okrs.md",
)

Embedder = Callable[[str], list[float]]


# ── corpus ────────────────────────────────────────────────────────────────────

@dataclass
class MemoryDoc:
    relative_path: str
    description: str
    body: str


def _memory_root() -> Path:
    return settings.memory_path


def load_corpus(root: Path | None = None) -> list[MemoryDoc]:
    """All on-demand memory docs (every *.md except the MEMORY.md index)."""
    base = root or _memory_root()
    docs: list[MemoryDoc] = []
    if not base.is_dir():
        return docs
    for path in sorted(base.rglob("*.md")):
        if path.name == _INDEX_FILE:
            continue
        try:
            post = frontmatter.load(str(path))
        except Exception as exc:  # noqa: BLE001 — skip unparseable
            logger.debug(f"retrieval: skip {path} ({exc})")
            continue
        docs.append(
            MemoryDoc(
                relative_path=str(path.relative_to(base)),
                description=str(post.get("description") or ""),
                body=post.content or "",
            )
        )
    return docs


# ── embedder ──────────────────────────────────────────────────────────────────

def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _stable_hash(token: str) -> int:
    """Process-independent hash (builtin hash() is PYTHONHASHSEED-randomized)."""
    return int.from_bytes(hashlib.md5(token.encode("utf-8")).digest()[:4], "little")


def hashed_embed(text: str, dim: int = _DIM) -> list[float]:
    """Deterministic hashed bag-of-tokens, L2-normalized."""
    vec = [0.0] * dim
    for tok in _tokens(text):
        vec[_stable_hash(tok) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


_embedder: Embedder = hashed_embed


def set_embedder(fn: Embedder) -> None:
    """Swap the embedding function (e.g. to fastembed) globally."""
    global _embedder
    _embedder = fn


def cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))  # both already L2-normalized


# ── retrieval ─────────────────────────────────────────────────────────────────

@dataclass
class RetrievalHit:
    path: str
    description: str
    score: float
    via: str  # "grep" | "vector" | "hybrid"


def grep_search(
    query: str, top_k: int = 5, root: Path | None = None
) -> list[RetrievalHit]:
    """Keyword arm: token-overlap count over path+description+body."""
    q_tokens = set(_tokens(query))
    hits: list[RetrievalHit] = []
    for doc in load_corpus(root):
        hay = f"{doc.relative_path} {doc.description} {doc.body}".lower()
        overlap = sum(1 for t in q_tokens if t in hay)
        if overlap:
            hits.append(
                RetrievalHit(doc.relative_path, doc.description, float(overlap), "grep")
            )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def vector_search(
    query: str, top_k: int = 5, root: Path | None = None
) -> list[RetrievalHit]:
    """Vector arm: cosine over the deterministic local embedding."""
    qv = _embedder(query)
    hits: list[RetrievalHit] = []
    for doc in load_corpus(root):
        score = cosine(qv, _embedder(f"{doc.description} {doc.body}"))
        if score > 0.0:
            hits.append(
                RetrievalHit(doc.relative_path, doc.description, score, "vector")
            )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def hybrid_search(
    query: str, top_k: int = 5, root: Path | None = None
) -> list[RetrievalHit]:
    """Merge grep (weight 1.0) + vector (weight 0.6); dedupe by path; rerank.

    A path surfaced by both arms is boosted and tagged ``hybrid``.
    """
    merged: dict[str, RetrievalHit] = {}

    for hit in grep_search(query, top_k * 2, root):
        merged[hit.path] = RetrievalHit(
            hit.path, hit.description, 1.0 * hit.score, "grep"
        )

    for hit in vector_search(query, top_k * 2, root):
        existing = merged.get(hit.path)
        if existing is None:
            merged[hit.path] = RetrievalHit(
                hit.path, hit.description, 0.6 * hit.score, "vector"
            )
        else:
            existing.score += 0.6 * hit.score
            existing.via = "hybrid"

    ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    return ranked[:top_k]


# ── Tier-1 goal-state ─────────────────────────────────────────────────────────

def has_active_goals(root: Path | None = None) -> bool:
    """True if a non-empty active-okrs file exists."""
    okr = (root or _memory_root()) / "business" / "active-okrs.md"
    try:
        return okr.exists() and bool(okr.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def load_tier1(root: Path | None = None) -> str:
    """Concat goal-state memory. Empty string when no active goals.

    Kept separate from Tier-0 so the prompt cache stays stable when goals
    change (blueprint 10.3).
    """
    base = root or _memory_root()
    if not has_active_goals(base):
        return ""
    parts: list[str] = []
    for rel in TIER1_PATHS:
        p = base / rel
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning(f"retrieval: gagal baca tier-1 {rel}: {exc}")
            continue
        if text:
            parts.append(f"### {rel}\n{text}")
    return "\n\n".join(parts)
