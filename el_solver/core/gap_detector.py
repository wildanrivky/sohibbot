"""Skill gap detector untuk Round 7 self-evolution."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from el_solver.config import PROJECT_ROOT
from el_solver.utils.db import get_connection
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_DB_PATH = PROJECT_ROOT / "data" / "el-solver.db"
_MEMORY_DIR = PROJECT_ROOT / "memory"


@dataclass
class SkillGapProposal:
    id: str
    skill_id: str
    skill_name: str
    description: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    risk_tier: int = 2
    side_effects: list[str] = field(default_factory=list)
    requires_skills: list[str] = field(default_factory=list)
    failure_pattern: str = ""
    source_failures: list[dict[str, Any]] = field(default_factory=list)
    status: str = "proposed"


def _get_conn() -> sqlite3.Connection:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    return conn


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "skill-gap"


def _parse_json_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [part.strip() for part in text.split(",") if part.strip()]
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        if isinstance(parsed, str):
            return [parsed.strip()] if parsed.strip() else []
    return []


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _normalize_pattern(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^a-z0-9\s_\-]", "", value)
    return value


def _extract_keywords(text: str, limit: int = 3) -> list[str]:
    stopwords = {
        "dan", "lalu", "kemudian", "untuk", "yang", "dengan", "di", "ke", "dari",
        "buat", "bikin", "buatkan", "tolong", "coba", "karena", "ada", "ini", "itu",
        "task", "agent", "skill", "tidak", "bisa", "punya", "punyai", "out", "of",
        "scope", "missing", "capability",
    }
    tokens = [tok.lower() for tok in re.findall(r"[a-zA-Z0-9\-]+", text or "")]
    filtered = [tok for tok in tokens if tok not in stopwords and len(tok) > 2]
    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(limit)]


def _guess_inputs(failure_pattern: str) -> list[str]:
    tokens = _extract_keywords(failure_pattern, limit=2)
    inputs = ["raw_message", "context"]
    if tokens:
        inputs.append("keywords:" + ",".join(tokens))
    return inputs


def _guess_outputs(failure_pattern: str) -> list[str]:
    tokens = _extract_keywords(failure_pattern, limit=2)
    outputs = ["analysis", "suggested_action"]
    if tokens:
        outputs.append("skill:" + ",".join(tokens))
    return outputs


def _gather_decision_failures(conn: sqlite3.Connection, cutoff: datetime) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT task_id, run_id, agent, signature, timestamp, decision, decision_reasons, confidence "
            "FROM decisions WHERE decision='reject' ORDER BY timestamp DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    for row in rows:
        ts = _parse_timestamp(row["timestamp"])
        if ts and ts < cutoff:
            continue
        reasons = _parse_json_list(row["decision_reasons"])
        reason_text = " ".join(reasons).lower()
        if "no_capability" not in reason_text and "no capability" not in reason_text:
            continue
        pattern = _normalize_pattern("no_capability")
        failures.append(
            {
                "source_type": "decision",
                "source_id": row["task_id"],
                "agent": row["agent"],
                "pattern": pattern,
                "summary": " ".join(reasons) or "no_capability",
                "timestamp": ts.isoformat() if ts else str(row["timestamp"] or ""),
            }
        )
    return failures


def _gather_delegation_failures(conn: sqlite3.Connection, cutoff: datetime) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id, parent_task_id, child_task_id, child_agent, status, output_summary, started_at, finished_at "
            "FROM delegations WHERE status='error' ORDER BY COALESCE(finished_at, started_at) DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return failures

    regex = re.compile(r"(tidak punya skill|out of scope)", re.IGNORECASE)
    for row in rows:
        ts = _parse_timestamp(row["finished_at"] or row["started_at"])
        if ts and ts < cutoff:
            continue
        summary = row["output_summary"] or ""
        if not regex.search(summary):
            continue
        match = regex.search(summary)
        pattern = _normalize_pattern(match.group(1) if match else "out of scope")
        failures.append(
            {
                "source_type": "delegation",
                "source_id": row["id"],
                "agent": row["child_agent"],
                "pattern": pattern,
                "summary": summary,
                "timestamp": ts.isoformat() if ts else "",
            }
        )
    return failures


def _gather_pattern_failures(cutoff: datetime) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    conn = _get_conn()
    try:
        try:
            rows = conn.execute(
                "SELECT id, agent, report_date, file_path, status, created_at "
                "FROM pattern_reports ORDER BY created_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            return failures

        for row in rows:
            ts = _parse_timestamp(row["created_at"])
            if ts and ts < cutoff:
                continue
            file_path = Path(row["file_path"] or "")
            if not file_path:
                continue
            if not file_path.is_absolute():
                file_path = (PROJECT_ROOT / file_path).resolve()
            try:
                content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
            except Exception:
                content = ""
            if "missing capability" not in content.lower():
                continue
            failures.append(
                {
                    "source_type": "pattern_report",
                    "source_id": row["id"],
                    "agent": row["agent"],
                    "pattern": _normalize_pattern("missing capability"),
                    "summary": "missing capability",
                    "timestamp": ts.isoformat() if ts else "",
                }
            )
    finally:
        conn.close()
    return failures


def _build_proposal(pattern: str, failures: list[dict[str, Any]]) -> SkillGapProposal:
    keywords = _extract_keywords(pattern, limit=3)
    skill_slug = _slugify("skill " + " ".join(keywords or [pattern]))
    skill_name = " ".join(word.capitalize() for word in (keywords or pattern.split())) or "Skill Gap"
    description = f"Skill scaffold untuk menutup gap: {pattern}"
    requires = [kw for kw in keywords if kw]
    return SkillGapProposal(
        id=str(uuid.uuid4()),
        skill_id=skill_slug,
        skill_name=skill_name,
        description=description,
        inputs=_guess_inputs(pattern),
        outputs=_guess_outputs(pattern),
        risk_tier=2,
        side_effects=[],
        requires_skills=requires,
        failure_pattern=pattern,
        source_failures=failures,
        status="proposed",
    )


def detect_gaps(window_days: int = 7) -> list[SkillGapProposal]:
    """Scan failure signals dan grup per failure_pattern. Minimal 3 occurrence per cluster."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    conn = _get_conn()
    failures: list[dict[str, Any]] = []
    try:
        failures.extend(_gather_decision_failures(conn, cutoff))
        failures.extend(_gather_delegation_failures(conn, cutoff))
    finally:
        conn.close()

    failures.extend(_gather_pattern_failures(cutoff))

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for failure in failures:
        grouped[_normalize_pattern(str(failure.get("pattern") or "unknown"))].append(failure)

    proposals: list[SkillGapProposal] = []
    for pattern, group in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(group) < 3:
            continue
        proposals.append(_build_proposal(pattern, group))
    return proposals


def save_proposal(proposal: SkillGapProposal) -> str:
    """Insert proposal ke skill_proposals."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO skill_proposals
               (id, skill_id, skill_name, description, inputs, outputs, risk_tier,
                side_effects, requires_skills, failure_pattern, source_failures,
                status, generated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                proposal.id,
                proposal.skill_id,
                proposal.skill_name,
                proposal.description,
                json.dumps(proposal.inputs, ensure_ascii=False),
                json.dumps(proposal.outputs, ensure_ascii=False),
                proposal.risk_tier,
                json.dumps(proposal.side_effects, ensure_ascii=False),
                json.dumps(proposal.requires_skills, ensure_ascii=False),
                proposal.failure_pattern,
                json.dumps(proposal.source_failures, ensure_ascii=False),
                proposal.status,
            ),
        )
        conn.commit()
        return proposal.id
    finally:
        conn.close()


def run_gap_detector(window_days: int = 7) -> list[str]:
    """Detect dan simpan proposal baru."""
    proposals = detect_gaps(window_days=window_days)
    saved: list[str] = []
    for proposal in proposals:
        try:
            saved.append(save_proposal(proposal))
        except sqlite3.IntegrityError:
            logger.info(f"gap_detector: proposal sudah ada, skip {proposal.skill_id}")
        except Exception as exc:
            logger.warning(f"gap_detector: gagal simpan proposal {proposal.skill_id}: {exc}")
    return saved


def detect_single_gap(
    agent: str,
    failure_description: str,
    source_type: str = "runtime",
) -> str | None:
    """Buat skill gap proposal langsung dari 1 failure — tanpa tunggu batch mingguan.

    Dipakai di scheduler dan handler saat task gagal real-time.
    Skip kalau proposal dengan pattern yang sama sudah ada dalam 7 hari terakhir.
    """
    if not failure_description or not agent:
        return None

    pattern = _normalize_pattern(failure_description[:200])
    if not pattern:
        return None

    # Jangan duplikat proposal yang sudah ada recent
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM skill_proposals WHERE failure_pattern=? AND status NOT IN ('rejected','merged') "
            "AND generated_at > datetime('now', '-7 days')",
            (pattern,),
        ).fetchone()
        if existing:
            logger.debug(f"gap_detector[realtime]: pattern '{pattern[:60]}' sudah ada proposal, skip")
            return None
    except Exception:
        pass
    finally:
        conn.close()

    failure_record: dict[str, Any] = {
        "source_type": source_type,
        "source_id": str(uuid.uuid4()),
        "agent": agent,
        "pattern": pattern,
        "summary": failure_description[:300],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    proposal = _build_proposal(pattern, [failure_record])
    proposal.source_failures = [failure_record]

    try:
        proposal_id = save_proposal(proposal)
        logger.info(
            f"gap_detector[realtime]: proposal '{proposal.skill_name}' dibuat untuk agent '{agent}'"
        )
        return proposal_id
    except sqlite3.IntegrityError:
        logger.debug(f"gap_detector[realtime]: proposal sudah ada (race), skip")
        return None
    except Exception as exc:
        logger.warning(f"gap_detector[realtime]: gagal simpan proposal: {exc}")
        return None
