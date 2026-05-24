"""
CLI channel — one-shot agent invocation via Orchestrator.

Pemakaian:
  el-solver tell "halo, ingat namaku Wildan"
  el-solver tell "buatkan agent rangkum berita"
  el-solver create-agent "rangkum berita teknologi tiap pagi"
  el-solver agent news-summarizer run "rangkum sekarang"
  el-solver agents list
  el-solver memory list
  el-solver memory show projects/campaign-ramadhan.md
  el-solver db migrate
  el-solver web                # start web dashboard di 127.0.0.1:8000
  el-solver web --port 8765    # custom port
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from el_solver import memory
from el_solver.channels import handler as msg_handler
from el_solver.config import settings
from el_solver.core.orchestrator import IntentResult, Mode, get_orchestrator
from el_solver.core.registry import AgentRegistry
from el_solver.utils import db as db_utils
from el_solver.utils.logger import get_logger

logger = get_logger(__name__)
console = Console()

_CLI_USER_ID = "wildan-cli"


# ── tell ─────────────────────────────────────────────────────────────────────

def cmd_tell(args: argparse.Namespace) -> int:
    message = " ".join(args.message).strip()
    if not message:
        console.print("[red]Pesan kosong.[/red]")
        return 2

    return _dispatch(message)


def _dispatch(message: str) -> int:
    """Classify pesan → handle → print response. Handle approval via stdin."""
    orch = get_orchestrator(llm_fallback=False)
    intent = orch.classify(message)
    console.print(f"[dim]→ Mode: {intent.mode.value} (conf {intent.confidence:.2f})[/dim]")

    response = asyncio.run(
        msg_handler.handle(intent, channel="cli", user_id=_CLI_USER_ID)
    )

    if response.needs_approval and response.approval_request_id:
        console.print()
        console.print(Panel(response.text, title="Plan Agent", border_style="yellow"))
        console.print("[bold yellow]Approve? [y/N]:[/bold yellow] ", end="")
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer == "y":
            console.print("[dim]→ Membuat agent…[/dim]")
            if response.pending_plan is not None:
                final = asyncio.run(
                    msg_handler.materialize_after_approval(
                        response.pending_plan, "cli", _CLI_USER_ID
                    )
                )
            else:
                console.print("[red]Plan tidak ditemukan di memory. Coba kirim ulang.[/red]")
                return 1
            console.print()
            console.print(Panel(final.text or "[empty]", title="EL SOLVER", border_style="green"))
        else:
            console.print("[dim]Agent batal dibuat.[/dim]")
        return 0

    console.print()
    console.print(Panel(response.text or "[empty]", title="EL SOLVER", border_style="cyan"))
    return 0


# ── create-agent ──────────────────────────────────────────────────────────────

def cmd_create_agent(args: argparse.Namespace) -> int:
    description = " ".join(args.description).strip()
    if not description:
        console.print("[red]Deskripsi agent kosong.[/red]")
        return 2

    # Paksa ke CREATE_AGENT mode
    intent = IntentResult(
        mode=Mode.CREATE_AGENT,
        confidence=1.0,
        raw_message=description,
    )
    response = asyncio.run(
        msg_handler.handle(intent, channel="cli", user_id=_CLI_USER_ID)
    )

    if response.needs_approval and response.approval_request_id:
        console.print()
        console.print(Panel(response.text, title="Plan Agent", border_style="yellow"))
        console.print("[bold yellow]Approve? [y/N]:[/bold yellow] ", end="")
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer == "y" and response.pending_plan is not None:
            console.print("[dim]→ Membuat agent…[/dim]")
            final = asyncio.run(
                msg_handler.materialize_after_approval(
                    response.pending_plan, "cli", _CLI_USER_ID
                )
            )
            console.print(Panel(final.text or "[empty]", title="EL SOLVER", border_style="green"))
        else:
            console.print("[dim]Agent batal dibuat.[/dim]")
        return 0

    console.print(Panel(response.text or "[empty]", title="EL SOLVER", border_style="green"))
    return 0


# ── agent run ─────────────────────────────────────────────────────────────────

def cmd_agent_run(args: argparse.Namespace) -> int:
    agent_name = args.name
    agent_input = " ".join(args.input).strip()

    intent = IntentResult(
        mode=Mode.INVOKE_AGENT,
        confidence=1.0,
        raw_message=agent_input or f"run {agent_name}",
        agent_name=agent_name,
    )
    console.print(f"[dim]→ Invoke agent '{agent_name}'…[/dim]")
    response = asyncio.run(
        msg_handler.handle(intent, channel="cli", user_id=_CLI_USER_ID)
    )
    console.print()
    console.print(Panel(response.text or "[empty]", title=f"Agent: {agent_name}", border_style="cyan"))
    return 0


# ── agents list ───────────────────────────────────────────────────────────────

def cmd_agents_list(args: argparse.Namespace) -> int:
    reg = AgentRegistry()
    agents = reg.list_all()
    if not agents:
        console.print("[yellow]Belum ada agent terdaftar.[/yellow]")
        return 0
    console.print(f"[bold]Agent terdaftar ({len(agents)}):[/bold]")
    for a in agents:
        status_color = "green" if a.is_active else "dim"
        console.print(
            f"  [{status_color}]{a.name}[/{status_color}] "
            f"({a.archetype}) — {a.role_description[:60]}"
        )
    return 0


# ── agents capabilities ───────────────────────────────────────────────────────

def cmd_agents_capabilities(args: argparse.Namespace) -> int:
    from el_solver.agents.base import scan_agents
    from el_solver.config import PROJECT_ROOT

    infos = scan_agents(PROJECT_ROOT / "agents")
    if not infos:
        console.print("[yellow]Tidak ada agent dengan manifest.[/yellow]")
        return 0
    console.print(f"[bold]Agent capabilities ({len(infos)}):[/bold]")
    for info in infos:
        color = "green" if info.is_active else "dim"
        caps = ", ".join(info.capabilities) or "(belum dideklarasikan)"
        console.print(
            f"  [{color}]{info.name}[/{color}] ({info.archetype}) "
            f"[{info.status}]\n      → {caps}"
        )
    return 0


def cmd_agents_sync(args: argparse.Namespace) -> int:
    from el_solver.core.registry import sync_agents_to_registry

    synced = sync_agents_to_registry()
    console.print(
        f"[green]Synced {len(synced)} agent manifest → registry:[/green] "
        f"{', '.join(synced) or '(none)'}"
    )
    return 0


# ── kpi ───────────────────────────────────────────────────────────────────────

def cmd_kpi_log(args: argparse.Namespace) -> int:
    from el_solver.core.kpi_ingest import KpiError, log_kpi

    note = " ".join(args.note).strip() if args.note else ""
    try:
        row_id = log_kpi(args.metric, args.value, note)
    except KpiError as exc:
        console.print(f"[red]KPI invalid:[/red] {exc}")
        return 2
    console.print(
        f"[green]KPI logged[/green] #{row_id}: {args.metric} = {args.value}"
        + (f" — {note}" if note else "")
    )
    return 0


def cmd_selfeval_report(args: argparse.Namespace) -> int:
    from el_solver.core.self_eval import self_eval_report

    console.print(self_eval_report(trace_days=args.days))
    return 0


def cmd_selfeval_retro(args: argparse.Namespace) -> int:
    from el_solver.core.self_eval import improvement_retrospective

    console.print(improvement_retrospective())
    return 0


def cmd_portfolio_review(args: argparse.Namespace) -> int:
    from el_solver.core.portfolio_planner import weekly_portfolio_review

    console.print(weekly_portfolio_review().to_markdown())
    return 0


def cmd_portfolio_initiative(args: argparse.Namespace) -> int:
    from el_solver.core.initiative import render_initiatives, scan_initiatives

    console.print(render_initiatives(scan_initiatives()))
    return 0


def cmd_portfolio_cycle(args: argparse.Namespace) -> int:
    from el_solver.core.initiative import autonomous_cycle

    console.print(autonomous_cycle().render())
    return 0


def cmd_autonomy(args: argparse.Namespace) -> int:
    from el_solver.core.autonomy import autonomy_report

    console.print(autonomy_report(days=args.days))
    return 0


def cmd_routine(args: argparse.Namespace) -> int:
    from el_solver.routines import daily_standup, weekly_review

    console.print(weekly_review() if args.kind == "weekly" else daily_standup())
    return 0


def cmd_decisions(args: argparse.Namespace) -> int:
    from el_solver.config import settings
    from el_solver.core.decision_engine import retrospective

    retro = retrospective(days=args.days)
    console.print(
        f"[bold]Decision retrospective ({retro['days']}d):[/bold] "
        f"total={retro['total']} autonomous_rate={retro['autonomous_rate']:.0%} "
        f"guardrail_blocks={retro['guardrail_blocks']}"
    )
    for pol, n in retro["policy_counts"].items():
        console.print(f"  {pol}: {n}")

    dec_dir = settings.memory_path / "decisions"
    pending = sorted(dec_dir.glob("*.md")) if dec_dir.is_dir() else []
    if pending:
        console.print(f"[bold]Decision records ({len(pending)}):[/bold]")
        for p in pending[-args.days:]:
            console.print(f"  {p.name}")
    return 0


def cmd_report_weekly(args: argparse.Namespace) -> int:
    from el_solver.core.eval import weekly_report

    console.print(weekly_report(days=args.days))
    return 0


def cmd_causal_advise(args: argparse.Namespace) -> int:
    from el_solver.core.causal import advise

    query = " ".join(args.query).strip()
    entries = advise(query)
    if not entries:
        console.print(f"[yellow]Tidak ada causal prior untuk '{query}'.[/yellow]")
        return 0
    console.print(f"[bold]Causal priors untuk '{query}':[/bold]")
    for e in entries:
        console.print(
            f"  [{e.confidence_tier()}] {e.id}: {e.action} → "
            f"{e.metric} ({e.expected_effect}) [n={e.observations}]"
        )
    return 0


def cmd_kpi_show(args: argparse.Namespace) -> int:
    from el_solver.core.kpi_ingest import recent

    points = recent(metric=args.metric, unit=args.unit, limit=args.limit)
    if not points:
        console.print("[yellow]Belum ada KPI snapshot.[/yellow]")
        return 0
    console.print(f"[bold]KPI snapshots ({len(points)}):[/bold]")
    for p in points:
        note = f" — {p.note}" if p.note else ""
        console.print(
            f"  [{p.ts}] {p.metric} = {p.value} "
            f"[dim]({p.unit}/{p.source})[/dim]{note}"
        )
    return 0


# ── memory ────────────────────────────────────────────────────────────────────

def cmd_memory_list(args: argparse.Namespace) -> int:
    entries = memory.list_all(exclude_always=False)
    if not entries:
        console.print("[yellow]Memory kosong.[/yellow]")
        return 0
    console.print(f"[bold]Memory ({len(entries)} entries):[/bold]")
    for e in entries:
        console.print(f"  {e.relative_path} — {e.description or '(no desc)'}")
    return 0


def cmd_memory_show(args: argparse.Namespace) -> int:
    rel = args.path
    entry = memory.get(rel)
    if not entry:
        console.print(f"[red]File tidak ada:[/red] {rel}")
        return 1
    content = entry.path.read_text(encoding="utf-8")
    console.print(Panel(content, title=rel, border_style="green"))
    return 0


def cmd_memory_core(args: argparse.Namespace) -> int:
    core = memory.load_core()
    console.print(Panel(core, title="Memory inti (tier always)", border_style="yellow"))
    return 0


# ── web ───────────────────────────────────────────────────────────────────────

def cmd_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn tidak terinstall. Jalankan: pip install uvicorn[standard][/red]")
        return 1

    port = args.port
    console.print(f"[green]Starting El Solver web dashboard...[/green]")
    console.print(f"[dim]→ http://127.0.0.1:{port}[/dim]")
    uvicorn.run(
        "el_solver.web.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
    )
    return 0


# ── scheduler ─────────────────────────────────────────────────────────────────

def cmd_scheduler_start(args: argparse.Namespace) -> int:
    try:
        from el_solver.core.scheduler import start
    except ImportError:
        console.print("[red]apscheduler tidak terinstall. Jalankan: pip install apscheduler>=3.10[/red]")
        return 1
    console.print("[green]Scheduler dimulai (foreground)...[/green]")
    start(foreground=True)
    return 0


def cmd_scheduler_daemon(args: argparse.Namespace) -> int:
    import subprocess
    import sys
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.wildan.elsolver-scheduler.plist"
    if not plist_path.exists():
        console.print(f"[red]Plist tidak ditemukan:[/red] {plist_path}")
        console.print("Buat dulu dengan: el-solver scheduler install-plist")
        return 1
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]Scheduler daemon dimulai via launchd.[/green]")
    else:
        console.print(f"[yellow]launchctl output:[/yellow] {result.stderr or result.stdout}")
    return result.returncode


def cmd_scheduler_install_plist(args: argparse.Namespace) -> int:
    import sys
    python_exec = sys.executable
    el_solver_bin = str(Path(python_exec).parent / "el-solver")

    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.wildan.elsolver-scheduler.plist"

    log_dir = Path.home() / "Library" / "Logs" / "ElSolver"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.wildan.elsolver-scheduler</string>
    <key>ProgramArguments</key>
    <array>
        <string>{el_solver_bin}</string>
        <string>scheduler</string>
        <string>start</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/scheduler.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/scheduler-error.log</string>
    <key>WorkingDirectory</key>
    <string>{Path(__file__).parent.parent.parent}</string>
</dict>
</plist>
"""
    plist_path.write_text(plist_content)
    console.print(f"[green]Plist ditulis ke:[/green] {plist_path}")
    console.print("Jalankan: launchctl load " + str(plist_path))
    return 0


# ── db ────────────────────────────────────────────────────────────────────────

def cmd_db_migrate(args: argparse.Namespace) -> int:
    try:
        newly_applied = db_utils.migrate()
    except Exception as e:
        console.print(f"[red]Migrasi gagal:[/red] {e}")
        return 1

    if newly_applied:
        console.print(f"[green]Migrasi selesai:[/green] {', '.join(newly_applied)}")
    else:
        console.print("[dim]Sudah up-to-date.[/dim]")
    return 0


# ── Parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="el-solver", description="Asisten pribadi Wildan.")
    sub = p.add_subparsers(dest="cmd", required=True)

    # tell
    p_tell = sub.add_parser("tell", help="Kirim pesan ke agent (auto-classify mode)")
    p_tell.add_argument("message", nargs="+", help="Pesan untuk agent")
    p_tell.add_argument("--model", default=None, help="(deprecated) Override model")
    p_tell.set_defaults(func=cmd_tell)

    # create-agent
    p_ca = sub.add_parser("create-agent", help="Buat agent baru (shortcut CREATE_AGENT mode)")
    p_ca.add_argument("description", nargs="+", help="Deskripsi agent yang ingin dibuat")
    p_ca.set_defaults(func=cmd_create_agent)

    # agent <name> run "<input>"
    p_ag = sub.add_parser("agent", help="Invoke atau manage satu agent")
    sub_ag = p_ag.add_subparsers(dest="ag_cmd", required=True)
    p_ag_run = sub_ag.add_parser("run", help="Jalankan agent dengan input")
    p_ag_run.add_argument("name", help="Nama agent (slug)")
    p_ag_run.add_argument("input", nargs="*", help="Input untuk agent")
    p_ag_run.set_defaults(func=cmd_agent_run)

    # agents list
    p_ags = sub.add_parser("agents", help="Operasi multi-agent")
    sub_ags = p_ags.add_subparsers(dest="ags_cmd", required=True)
    p_ags_list = sub_ags.add_parser("list", help="List semua agent terdaftar")
    p_ags_list.set_defaults(func=cmd_agents_list)

    p_ags_caps = sub_ags.add_parser(
        "capabilities", help="List agent + capability dari manifest"
    )
    p_ags_caps.set_defaults(func=cmd_agents_capabilities)

    p_ags_sync = sub_ags.add_parser(
        "sync", help="Sync manifest agent → registry (capabilities)"
    )
    p_ags_sync.set_defaults(func=cmd_agents_sync)

    # memory
    p_mem = sub.add_parser("memory", help="Akses memory")
    sub_mem = p_mem.add_subparsers(dest="mem_cmd", required=True)

    p_mem_list = sub_mem.add_parser("list", help="List semua memory entries")
    p_mem_list.set_defaults(func=cmd_memory_list)

    p_mem_show = sub_mem.add_parser("show", help="Tampilkan isi file memory")
    p_mem_show.add_argument("path", help="Path relatif, mis. 'user/profile.md'")
    p_mem_show.set_defaults(func=cmd_memory_show)

    p_mem_core = sub_mem.add_parser("core", help="Print memory inti (tier always)")
    p_mem_core.set_defaults(func=cmd_memory_core)

    # web
    p_web = sub.add_parser("web", help="Start web dashboard (127.0.0.1:8000)")
    p_web.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p_web.set_defaults(func=cmd_web)

    # scheduler
    p_sched = sub.add_parser("scheduler", help="Kelola scheduler cron agent")
    sub_sched = p_sched.add_subparsers(dest="sched_cmd", required=True)

    p_sched_start = sub_sched.add_parser("start", help="Jalankan scheduler (foreground, debug)")
    p_sched_start.set_defaults(func=cmd_scheduler_start)

    p_sched_daemon = sub_sched.add_parser("daemon", help="Load scheduler via launchd")
    p_sched_daemon.set_defaults(func=cmd_scheduler_daemon)

    p_sched_plist = sub_sched.add_parser("install-plist", help="Tulis launchd plist ke ~/Library/LaunchAgents/")
    p_sched_plist.set_defaults(func=cmd_scheduler_install_plist)

    # kpi
    p_kpi = sub.add_parser("kpi", help="Log & lihat KPI bisnis")
    sub_kpi = p_kpi.add_subparsers(dest="kpi_cmd", required=True)

    p_kpi_log = sub_kpi.add_parser("log", help="Catat 1 KPI snapshot")
    p_kpi_log.add_argument("metric", help="mis. myagent.metric")
    p_kpi_log.add_argument("value", help="nilai numerik")
    p_kpi_log.add_argument("note", nargs="*", help="catatan opsional")
    p_kpi_log.set_defaults(func=cmd_kpi_log)

    p_kpi_show = sub_kpi.add_parser("show", help="Tampilkan KPI terbaru")
    p_kpi_show.add_argument("--metric", default=None, help="filter metric")
    p_kpi_show.add_argument("--unit", default=None, help="filter unit")
    p_kpi_show.add_argument("--limit", type=int, default=20, help="max baris")
    p_kpi_show.set_defaults(func=cmd_kpi_show)

    # causal
    p_cau = sub.add_parser("causal", help="Causal model bisnis")
    sub_cau = p_cau.add_subparsers(dest="cau_cmd", required=True)
    p_cau_adv = sub_cau.add_parser("advise", help="Prior causal untuk metric/aksi")
    p_cau_adv.add_argument("query", nargs="+", help="metric atau kata kunci aksi")
    p_cau_adv.set_defaults(func=cmd_causal_advise)

    # selfeval
    p_se = sub.add_parser("selfeval", help="Self-eval vs golden set")
    sub_se = p_se.add_subparsers(dest="se_cmd", required=True)
    p_se_rep = sub_se.add_parser("report", help="Golden pass-rate + flags")
    p_se_rep.add_argument("--days", type=int, default=7, help="trace window")
    p_se_rep.set_defaults(func=cmd_selfeval_report)
    p_se_retro = sub_se.add_parser("retro", help="Improvement retrospective")
    p_se_retro.set_defaults(func=cmd_selfeval_retro)

    # portfolio
    p_pf = sub.add_parser("portfolio", help="Portfolio GM")
    sub_pf = p_pf.add_subparsers(dest="pf_cmd", required=True)
    p_pf_rev = sub_pf.add_parser("review", help="Weekly portfolio review")
    p_pf_rev.set_defaults(func=cmd_portfolio_review)
    p_pf_init = sub_pf.add_parser("initiative", help="Scan initiatives")
    p_pf_init.set_defaults(func=cmd_portfolio_initiative)
    p_pf_cyc = sub_pf.add_parser("cycle", help="Autonomous cycle (dry, Wildan-gated)")
    p_pf_cyc.set_defaults(func=cmd_portfolio_cycle)

    # autonomy
    p_au = sub.add_parser("autonomy", help="Autonomy / touch-time metric")
    p_au.add_argument("--days", type=int, default=30, help="window hari")
    p_au.set_defaults(func=cmd_autonomy)

    # routine
    p_rt = sub.add_parser("routine", help="Cadence routine digest")
    p_rt.add_argument(
        "kind", choices=["daily", "weekly"], help="standup harian / review mingguan"
    )
    p_rt.set_defaults(func=cmd_routine)

    # decisions
    p_dec = sub.add_parser("decisions", help="Retrospektif decision log")
    p_dec.add_argument("--days", type=int, default=30, help="window hari")
    p_dec.set_defaults(func=cmd_decisions)

    # report
    p_rep = sub.add_parser("report", help="Laporan eval")
    sub_rep = p_rep.add_subparsers(dest="rep_cmd", required=True)
    p_rep_wk = sub_rep.add_parser("weekly", help="Agent performance + KPI")
    p_rep_wk.add_argument("--days", type=int, default=7, help="window hari")
    p_rep_wk.set_defaults(func=cmd_report_weekly)

    # db
    p_db = sub.add_parser("db", help="Database utilities")
    sub_db = p_db.add_subparsers(dest="db_cmd", required=True)

    p_db_migrate = sub_db.add_parser("migrate", help="Jalankan migrasi SQL (idempotent)")
    p_db_migrate.set_defaults(func=cmd_db_migrate)

    return p


# ── Entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings.ensure_dirs()
    # Auto-migrate saat startup (idempotent, ~1ms jika sudah up-to-date)
    db_utils.migrate()
    return args.func(args)


def telegram_main() -> None:
    """Entry point untuk el-solver-telegram — jalankan bot Telegram terpadu."""
    import runpy
    bot_script = Path(__file__).parent.parent.parent / "scripts" / "claude_telegram.py"
    runpy.run_path(str(bot_script), run_name="__main__")


if __name__ == "__main__":
    sys.exit(main())
