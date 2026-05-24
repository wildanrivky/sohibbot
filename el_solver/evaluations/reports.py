"""
Weekly Review Report — aggregate traces dari seminggu terakhir, kirim ke Telegram.

Usage:
    report = generate_weekly_report()
    print(report.telegram_message())
    # atau:
    python -m el_solver.evaluations.reports
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from el_solver.config import PROJECT_ROOT
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)

TRACES_DIR = PROJECT_ROOT / "data" / "traces"
REPORTS_DIR = PROJECT_ROOT / "data" / "reports"


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class AgentWeekStats:
    agent_name: str
    total_runs: int = 0
    success_count: int = 0
    error_count: int = 0
    total_duration_ms: float = 0.0
    tool_calls_total: int = 0
    slowest_run_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def fail_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.error_count / self.total_runs

    @property
    def avg_duration_ms(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.total_duration_ms / self.total_runs


@dataclass
class WeeklyReport:
    period_start: str
    period_end: str
    total_runs: int = 0
    total_success: int = 0
    total_errors: int = 0
    agents: list[AgentWeekStats] = field(default_factory=list)
    top_tools: list[tuple[str, int]] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def overall_fail_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.total_errors / self.total_runs

    def telegram_message(self) -> str:
        """Format ringkas untuk Telegram."""
        lines = [
            "📊 Weekly Report El Solver",
            f"Period: {self.period_start[:10]} → {self.period_end[:10]}",
            "",
            f"Total runs: {self.total_runs}",
            f"Success: {self.total_success} | Error: {self.total_errors}",
        ]

        if self.total_runs > 0:
            fail_pct = self.overall_fail_rate * 100
            lines.append(f"Fail rate: {fail_pct:.1f}%")

        if self.agents:
            lines.append("")
            lines.append("📌 Per Agent:")
            for a in sorted(self.agents, key=lambda x: x.total_runs, reverse=True)[:5]:
                status = "✓" if a.fail_rate < 0.1 else "⚠"
                lines.append(
                    f"  {status} {a.agent_name}: {a.total_runs} runs, "
                    f"{a.fail_rate*100:.0f}% fail, "
                    f"{a.avg_duration_ms:.0f}ms avg"
                )

        if self.top_tools:
            lines.append("")
            lines.append("🔧 Top Tools:")
            for tool_name, count in self.top_tools[:5]:
                lines.append(f"  • {tool_name}: {count}x")

        bad_agents = [a for a in self.agents if a.fail_rate >= 0.3]
        if bad_agents:
            lines.append("")
            lines.append("⚠️ Perlu perhatian (fail rate ≥30%):")
            for a in bad_agents:
                lines.append(f"  - {a.agent_name}: {a.fail_rate*100:.0f}% fail")

        return "\n".join(lines)

    def markdown_report(self) -> str:
        """Format lengkap untuk disimpan ke file."""
        lines = [
            "# Weekly Report — El Solver",
            f"**Period**: {self.period_start[:10]} → {self.period_end[:10]}",
            f"**Generated**: {self.generated_at[:19]}",
            "",
            "## Summary",
            f"- Total runs: {self.total_runs}",
            f"- Success: {self.total_success}",
            f"- Errors: {self.total_errors}",
            f"- Fail rate: {self.overall_fail_rate*100:.1f}%",
            "",
        ]

        if self.agents:
            lines.append("## Per Agent")
            lines.append("")
            lines.append("| Agent | Runs | Success | Errors | Avg ms | Slowest ms |")
            lines.append("|---|---|---|---|---|---|")
            for a in sorted(self.agents, key=lambda x: x.total_runs, reverse=True):
                lines.append(
                    f"| {a.agent_name} | {a.total_runs} | {a.success_count} | "
                    f"{a.error_count} | {a.avg_duration_ms:.0f} | {a.slowest_run_ms:.0f} |"
                )
            lines.append("")

        if self.top_tools:
            lines.append("## Tool Usage")
            lines.append("")
            for tool_name, count in self.top_tools:
                lines.append(f"- `{tool_name}`: {count} calls")
            lines.append("")

        return "\n".join(lines)


# ── Trace reader ──────────────────────────────────────────────────────────────

def _iter_traces_for_period(
    traces_dir: Path,
    days: int = 7,
) -> list[dict[str, Any]]:
    """Baca semua traces dari N hari terakhir."""
    if not traces_dir.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    all_entries: list[dict[str, Any]] = []

    for agent_dir in sorted(traces_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        for jsonl_file in sorted(agent_dir.glob("*.jsonl")):
            try:
                file_date = datetime.strptime(jsonl_file.stem, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            if file_date < cutoff:
                continue
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        entry = json.loads(line)
                        entry["_agent_dir"] = agent_dir.name
                        all_entries.append(entry)
                    except json.JSONDecodeError:
                        pass

    return all_entries


# ── Aggregator ────────────────────────────────────────────────────────────────

def _aggregate(entries: list[dict[str, Any]]) -> tuple[
    dict[str, AgentWeekStats],
    dict[str, int],
]:
    """Aggregate trace entries ke per-agent stats dan tool usage."""
    agent_stats: dict[str, AgentWeekStats] = {}
    tool_counts: dict[str, int] = defaultdict(int)

    for entry in entries:
        agent_name = entry.get("agent_name") or entry.get("_agent_dir", "unknown")
        if agent_name not in agent_stats:
            agent_stats[agent_name] = AgentWeekStats(agent_name=agent_name)
        stats = agent_stats[agent_name]

        stats.total_runs += 1
        status = entry.get("status", "")
        if status == "success":
            stats.success_count += 1
        else:
            stats.error_count += 1
            err = entry.get("error") or status
            if err:
                stats.errors.append(err[:100])

        dur = entry.get("duration_ms") or 0.0
        stats.total_duration_ms += dur
        if dur > stats.slowest_run_ms:
            stats.slowest_run_ms = dur

        for tc in entry.get("tool_calls", []):
            tool_name = tc.get("tool_name", "unknown")
            stats.tool_calls_total += 1
            tool_counts[tool_name] += 1

    return agent_stats, dict(tool_counts)


# ── Main generator ────────────────────────────────────────────────────────────

def generate_weekly_report(
    traces_dir: Path | None = None,
    days: int = 7,
    save: bool = True,
) -> WeeklyReport:
    """
    Generate weekly report dari traces dalam N hari terakhir.

    Args:
        traces_dir : override direktori traces (default: data/traces/)
        days       : jumlah hari ke belakang yang di-scan
        save       : kalau True, simpan markdown report ke data/reports/

    Returns:
        WeeklyReport
    """
    base = traces_dir or TRACES_DIR
    now = datetime.now(timezone.utc)
    period_start = (now - timedelta(days=days)).isoformat()
    period_end = now.isoformat()

    logger.info(f"reports: generating weekly report (last {days} days)...")
    entries = _iter_traces_for_period(base, days=days)
    logger.info(f"reports: {len(entries)} trace entries found")

    agent_stats, tool_counts = _aggregate(entries)

    top_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)

    report = WeeklyReport(
        period_start=period_start,
        period_end=period_end,
        total_runs=sum(s.total_runs for s in agent_stats.values()),
        total_success=sum(s.success_count for s in agent_stats.values()),
        total_errors=sum(s.error_count for s in agent_stats.values()),
        agents=list(agent_stats.values()),
        top_tools=top_tools,
    )

    if save and entries:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report_file = REPORTS_DIR / f"weekly-{now.strftime('%Y-%m-%d')}.md"
        report_file.write_text(report.markdown_report(), encoding="utf-8")
        logger.info(f"reports: saved to {report_file}")

    return report


if __name__ == "__main__":
    report = generate_weekly_report()
    print(report.telegram_message())
