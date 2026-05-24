"""Pattern Miner — weekly batch analysis dari lessons.md per agent.

Cron: setiap Senin 02:00 WIB via APScheduler.
Per agent: baca lessons.md 7 hari terakhir, min 5 entry → panggil Claude CLI
untuk summarize → tulis memory/pattern_report_<agent>_<date>.md + catat ke DB.

Auto-apply: rules dengan confidence >= 0.70 langsung di-inject ke versioned prompt.
Threshold confidence: entries >= 10 → 0.90, entries >= 7 → 0.75, < 7 → 0.50.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_MEMORY_DIR = _PROJECT_ROOT / "memory"
_DB_PATH = _PROJECT_ROOT / "data" / "el-solver.db"
_MIN_ENTRIES = 5


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def normalize_pattern_report_path(file_path: str | Path) -> Path:
    """Return absolute report path, regardless of how it was stored in DB."""
    path = Path(file_path)
    if not path.is_absolute():
        path = (_PROJECT_ROOT / path).resolve()
    return path


def backfill_pattern_report_paths() -> int:
    """Normalize stored pattern report paths to absolute paths."""
    conn = _get_conn()
    updated = 0
    try:
        rows = conn.execute("SELECT id, file_path FROM pattern_reports").fetchall()
        for row in rows:
            raw_path = row["file_path"] or ""
            if not raw_path:
                continue
            abs_path = normalize_pattern_report_path(raw_path)
            if str(abs_path) == raw_path:
                continue
            conn.execute(
                "UPDATE pattern_reports SET file_path=? WHERE id=?",
                (str(abs_path), row["id"]),
            )
            updated += 1
        if updated:
            conn.commit()
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return updated


def _parse_lessons(agent_name: str, days: int = 7) -> list[dict]:
    lessons_file = _MEMORY_DIR / agent_name / "lessons.md"
    if not lessons_file.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    entries: list[dict] = []
    for line in lessons_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts_str = entry.get("timestamp", "")
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts >= cutoff:
                    entries.append(entry)
            else:
                entries.append(entry)
        except (json.JSONDecodeError, ValueError):
            continue
    return entries


def _summarize_with_claude(agent_name: str, entries: list[dict]) -> str:
    entries_text = "\n".join(
        f"- [{e.get('timestamp', '?')[:10]}] Task: {e.get('task', '?')} | "
        f"Outcome: {e.get('outcome', '?')} | Lesson: {e.get('brief_lesson', '?')}"
        for e in entries
    )
    prompt = (
        f"Kamu adalah analyst AI agent. Analisis 7-day lessons dari agent '{agent_name}':\n\n"
        f"{entries_text}\n\n"
        "Identifikasi:\n"
        "1. Pattern berulang (minimal 2x muncul)\n"
        "2. Weakness yang konsisten\n"
        "3. Suggested rules — kalimat yang bisa langsung masuk system prompt agent\n\n"
        "Format output PERSIS seperti ini:\n\n"
        "## Patterns\n- [pattern 1]\n- [pattern 2]\n\n"
        "## Weaknesses\n- [weakness 1]\n\n"
        "## Suggested Rules\n1. [RULE] kalimat aturan 1\n2. [RULE] kalimat aturan 2\n\n"
        "Bahasa Indonesia. Singkat dan actionable. Tidak ada intro atau outro."
    )
    try:
        from el_solver.llm import call_claude_cli
        result, *_ = call_claude_cli(prompt, timeout=120)
        return result
    except Exception as exc:
        logger.error(f"pattern_miner: claude CLI gagal untuk {agent_name}: {exc}")
        return f"## Error\nFailed to generate summary: {exc}"


def extract_suggested_rules(content: str) -> str:
    """Ekstrak bagian '## Suggested Rules' dari report content."""
    if "## Suggested Rules" not in content:
        return ""
    rules_part = content.split("## Suggested Rules", 1)[1].strip()
    if "\n##" in rules_part:
        rules_part = rules_part[: rules_part.index("\n##")].strip()
    return rules_part


def _parse_rules_list(rules_text: str) -> list[str]:
    """Parse rules text jadi list of rule strings bersih."""
    rules = []
    for line in rules_text.splitlines():
        line = line.strip()
        # Match format "1. [RULE] teks" atau "- [RULE] teks"
        m = re.match(r"^(?:\d+\.|[-*])\s*(?:\[RULE\])?\s*(.+)$", line)
        if m:
            rule = m.group(1).strip()
            if rule:
                rules.append(rule)
    return rules


def _compute_confidence(entries_count: int) -> float:
    """Confidence score berdasarkan jumlah entries yang dianalisis."""
    if entries_count >= 10:
        return 0.90
    if entries_count >= 7:
        return 0.75
    return 0.50


def _get_latest_prompt_version(agent_name: str) -> tuple[int, Path | None]:
    """Return (latest_version_number, path). (0, None) kalau belum ada."""
    prompts_dir = _PROJECT_ROOT / "prompts"
    if not prompts_dir.exists():
        return 0, None
    pattern = re.compile(rf"^{re.escape(agent_name)}-v(\d+)\.md$")
    candidates: list[tuple[int, Path]] = []
    for f in prompts_dir.iterdir():
        m = pattern.match(f.name)
        if m:
            candidates.append((int(m.group(1)), f))
    if not candidates:
        return 0, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0]


def _auto_apply_rules(agent_name: str, report_content: str, entries_count: int) -> bool:
    """Auto-apply suggested rules ke versioned prompt kalau confidence cukup.

    Threshold: confidence >= 0.70. Return True kalau berhasil apply.
    Rules yang tidak auto-apply tetap tersedia di /inbox untuk manual review.
    """
    _AUTO_APPLY_THRESHOLD = 0.70
    confidence = _compute_confidence(entries_count)

    if confidence < _AUTO_APPLY_THRESHOLD:
        logger.info(
            f"pattern_miner[auto-apply]: skip {agent_name} — confidence {confidence:.2f} "
            f"< threshold {_AUTO_APPLY_THRESHOLD} ({entries_count} entries)"
        )
        return False

    rules_text = extract_suggested_rules(report_content)
    rules = _parse_rules_list(rules_text)
    if not rules:
        logger.info(f"pattern_miner[auto-apply]: tidak ada rules untuk {agent_name}")
        return False

    latest_ver, latest_path = _get_latest_prompt_version(agent_name)

    # Baca isi prompt lama kalau ada
    existing_content = ""
    if latest_path and latest_path.exists():
        try:
            existing_content = latest_path.read_text(encoding="utf-8")
        except Exception:
            pass

    # Cek apakah rules ini sudah ada di prompt (hindari duplikat)
    new_rules = [r for r in rules if r not in existing_content]
    if not new_rules:
        logger.info(f"pattern_miner[auto-apply]: semua rules sudah ada di prompt {agent_name}, skip")
        return False

    # Buat versi baru
    next_ver = latest_ver + 1
    prompts_dir = _PROJECT_ROOT / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    new_path = prompts_dir / f"{agent_name}-v{next_ver}.md"

    today = datetime.now().strftime("%Y-%m-%d")
    auto_apply_section = (
        f"\n\n---\n## Rules (auto-injected {today}, confidence {confidence:.0%})\n"
        + "\n".join(f"- {r}" for r in new_rules)
    )

    new_content = (existing_content.rstrip() if existing_content else f"# {agent_name} — system prompt") + auto_apply_section + "\n"
    new_path.write_text(new_content, encoding="utf-8")
    logger.info(f"pattern_miner[auto-apply]: wrote {new_path.name} ({len(new_rules)} rules)")

    # Git commit otomatis
    try:
        commit_msg = f"feat({agent_name}): auto-inject {len(new_rules)} rules dari pattern {today} (conf {confidence:.0%})"
        subprocess.run(
            ["git", "add", str(new_path)],
            cwd=str(_PROJECT_ROOT),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(_PROJECT_ROOT),
            check=True,
            capture_output=True,
        )
        logger.info(f"pattern_miner[auto-apply]: git commit OK — {commit_msg}")
    except subprocess.CalledProcessError as exc:
        logger.warning(f"pattern_miner[auto-apply]: git commit gagal (non-fatal): {exc.stderr.decode()[:200]}")

    return True


def _mine_agent(agent_name: str) -> str | None:
    entries = _parse_lessons(agent_name)
    if len(entries) < _MIN_ENTRIES:
        logger.info(f"pattern_miner: {agent_name} hanya {len(entries)} entry (min {_MIN_ENTRIES}), skip")
        return None

    logger.info(f"pattern_miner: mining {len(entries)} entries untuk {agent_name}")
    summary = _summarize_with_claude(agent_name, entries)

    today = datetime.now().strftime("%Y-%m-%d")
    report_path = normalize_pattern_report_path(_MEMORY_DIR / f"pattern_report_{agent_name}_{today}.md")
    report_content = (
        f"# Pattern Report — {agent_name} ({today})\n\n"
        f"**Agent**: `{agent_name}`  \n"
        f"**Entries analyzed**: {len(entries)} (last 7 days)  \n"
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"---\n\n"
        f"{summary}\n"
    )
    report_path.write_text(report_content, encoding="utf-8")
    logger.info(f"pattern_miner: report → {report_path.name}")

    # Auto-apply rules kalau confidence cukup
    auto_applied = False
    try:
        auto_applied = _auto_apply_rules(agent_name, report_content, len(entries))
    except Exception as exc:
        logger.warning(f"pattern_miner: auto-apply gagal (non-fatal): {exc}")

    report_status = "auto_applied" if auto_applied else "new"
    report_id = str(uuid.uuid4())
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO pattern_reports (id, agent, report_date, file_path, entry_count, status)
               VALUES (?,?,?,?,?,?)""",
            (report_id, agent_name, today, str(report_path), len(entries), report_status),
        )
        conn.commit()
    except Exception as exc:
        logger.error(f"pattern_miner: gagal simpan ke DB: {exc}")
    finally:
        conn.close()

    return str(report_path)


def run_pattern_miner() -> dict[str, str | None]:
    """Mine semua agent yang punya memory/<agent>/lessons.md."""
    logger.info("pattern_miner: mulai weekly run")
    results: dict[str, str | None] = {}
    if not _MEMORY_DIR.exists():
        return results
    for agent_dir in sorted(_MEMORY_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        if (agent_dir / "lessons.md").exists():
            results[agent_dir.name] = _mine_agent(agent_dir.name)
    logger.info(f"pattern_miner: selesai. Reports: {[v for v in results.values() if v]}")
    return results


def register_pattern_miner_cron(scheduler) -> None:
    """Register weekly cron: Senin 02:00 WIB."""
    try:
        from apscheduler.triggers.cron import CronTrigger
        job_id = "pattern_miner_weekly"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        scheduler.add_job(
            run_pattern_miner,
            trigger=CronTrigger(day_of_week="mon", hour=2, minute=0, timezone="Asia/Jakarta"),
            id=job_id,
            replace_existing=True,
        )
        logger.info("pattern_miner: registered cron Senin 02:00 WIB")
    except Exception as exc:
        logger.error(f"pattern_miner: gagal register cron: {exc}")
