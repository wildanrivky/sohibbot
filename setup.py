#!/usr/bin/env python3
"""
SohibBot Setup Wizard — cross-platform (Mac/Linux/Windows).
Jalankan: python setup.py
"""
from __future__ import annotations

import os
import shutil
import sys

# ── Bootstrap: auto-switch ke Python 3.11+ kalau versi aktif terlalu lama ──────
# Harus jalan SEBELUM import lain supaya bisa re-exec dengan aman.
if sys.version_info < (3, 11):
    _candidates = ["python3.13", "python3.12", "python3.11"]
    if sys.platform == "darwin":
        _candidates += [
            "/opt/homebrew/opt/python@3.13/bin/python3.13",
            "/opt/homebrew/opt/python@3.12/bin/python3.12",
            "/opt/homebrew/opt/python@3.11/bin/python3.11",
            "/usr/local/opt/python@3.11/bin/python3.11",
        ]
    for _c in _candidates:
        _p = shutil.which(_c) or (_c if os.path.exists(_c) else None)
        if _p:
            print(f"  ℹ️  Switching ke {_p} (Python {sys.version_info.major}.{sys.version_info.minor} terlalu lama)...")
            os.execv(_p, [_p] + sys.argv)
    # Tidak ada Python 3.11+ ditemukan sama sekali
    print("\n  ✗ Python 3.11+ dibutuhkan tapi tidak ditemukan di sistem kamu.")
    print("  Install dulu:\n    Mac:   brew install python@3.11")
    print("    Linux: sudo apt install python3.11 python3.11-venv")
    sys.exit(1)
# ───────────────────────────────────────────────────────────────────────────────

import json
import platform
import re
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path

WORKDIR = Path(__file__).parent.resolve()
PROGRESS_FILE = WORKDIR / ".setup_progress.json"
VENV_DIR = WORKDIR / ".venv"
OS = platform.system()  # "Darwin", "Linux", "Windows"


# ── Utilities ──────────────────────────────────────────────────────────────────

def banner(text: str) -> None:
    width = 56
    print("\n" + "=" * width)
    for line in text.strip().splitlines():
        print(f"   {line}")
    print("=" * width + "\n")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def err(msg: str) -> None:
    print(f"  ✗ {msg}")


def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    val = input(f"  {prompt}{hint}: ").strip()
    return val if val else default


def ask_yn(prompt: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    val = input(f"  {prompt} {hint}: ").strip().lower()
    if not val:
        return default
    return val in ("y", "ya", "yes", "1")


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
    return {}


def save_progress(data: dict) -> None:
    existing = load_progress()
    existing.update(data)
    PROGRESS_FILE.write_text(json.dumps(existing, indent=2))


def run(cmd: list[str], check=True, capture=False, cwd=None) -> subprocess.CompletedProcess:
    kwargs = {"cwd": cwd or WORKDIR}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    return subprocess.run(cmd, check=check, **kwargs)


# ── Step 1: Cek sistem ─────────────────────────────────────────────────────────

def step_check_system() -> dict:
    print("[1/7] Mengecek sistem...")

    # OS
    arch = platform.machine()
    ok(f"OS: {OS} ({platform.release()}, {arch})")

    # Python version
    py = sys.version_info
    if py < (3, 11):
        err(f"Python {py.major}.{py.minor} — minimal 3.11 dibutuhkan")
        print("\n  Cara upgrade Python:")
        if OS == "Darwin":
            print("  Mac:   brew install python@3.11")
        elif OS == "Linux":
            print("  Linux: sudo apt install python3.11 python3.11-venv")
        else:
            print("  Windows: download dari https://python.org/downloads (centang 'Add to PATH')")
        sys.exit(1)
    ok(f"Python {py.major}.{py.minor}.{py.micro}")

    # tmux (Mac/Linux only)
    if OS != "Windows":
        if shutil.which("tmux"):
            ok("tmux tersedia")
        else:
            err("tmux belum terinstall")
            if OS == "Darwin" and ask_yn("Install tmux via Homebrew?"):
                if shutil.which("brew"):
                    run(["brew", "install", "tmux"])
                    ok("tmux terinstall")
                else:
                    print("  Homebrew tidak ada. Install Homebrew dulu: https://brew.sh")
            elif OS == "Linux":
                print("  Install tmux: sudo apt install tmux")

    # Claude CLI
    claude_path = _find_claude_cli()
    if claude_path:
        ok(f"Claude CLI: {claude_path}")
        _verify_claude_auth(claude_path)
    else:
        err("Claude Code CLI belum ditemukan di PATH")
        print("\n  Claude Code adalah otak dari bot ini. Kamu perlu install dulu.")
        print("  Panduan: https://docs.anthropic.com/en/docs/claude-code/quickstart")
        if ask_yn("Buka panduan di browser?"):
            webbrowser.open("https://docs.anthropic.com/en/docs/claude-code/quickstart")
        input("\n  Setelah install dan login Claude Code, tekan ENTER untuk lanjut...")
        claude_path = _find_claude_cli()
        if not claude_path:
            print("  Masukkan path manual ke claude CLI:")
            claude_path = ask("Path claude CLI (contoh: /Users/kamu/.local/bin/claude)")

    return {"claude_path": str(claude_path) if claude_path else "claude"}


def _find_claude_cli() -> Path | None:
    # Cek PATH dulu
    found = shutil.which("claude")
    if found:
        return Path(found)
    # Common install locations
    candidates = []
    if OS != "Windows":
        home = Path.home()
        candidates = [
            home / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            home / ".npm-global" / "bin" / "claude",
        ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _verify_claude_auth(claude_path) -> None:
    try:
        result = run([str(claude_path), "--version"], capture=True, check=False)
        if result.returncode == 0:
            ok(f"Claude CLI authenticated ({result.stdout.strip()})")
        else:
            err("Claude CLI ada tapi mungkin belum login. Jalankan 'claude' sekali untuk login.")
    except Exception:
        pass


# ── Step 2: Identitas user ─────────────────────────────────────────────────────

def step_identity(progress: dict) -> dict:
    print("[2/7] Identitas kamu")

    existing_name = progress.get("user_name", "")
    existing_bot = progress.get("bot_name", "")
    existing_email = progress.get("user_email", "")
    existing_slug = progress.get("bot_slug", "")

    user_name = ask("Nama panggilan kamu?", existing_name)
    bot_name = ask("Nama bot (contoh: ARIA, NOVA, ALFRED)?", existing_bot or "SohibBot")
    user_email = ask("Email kamu?", existing_email)

    # Generate slug dari bot_name
    slug = re.sub(r"[^a-z0-9]+", "-", bot_name.lower()).strip("-")
    bot_slug = ask(f"Slug bot (untuk nama service)?", existing_slug or slug)
    service_label = f"com.{user_name.lower().replace(' ', '')}.{bot_slug}"

    ok(f"Nama bot: {bot_name} | Slug: {bot_slug}")
    return {
        "user_name": user_name,
        "bot_name": bot_name,
        "user_email": user_email,
        "bot_slug": bot_slug,
        "service_label": service_label,
    }


# ── Step 3: Telegram bot ───────────────────────────────────────────────────────

def step_telegram(progress: dict) -> dict:
    print("[3/7] Telegram Bot")

    existing_token = progress.get("bot_token", "")
    existing_owner = progress.get("owner_id", "")

    if not existing_token:
        print("\n  Kamu butuh bot Telegram dari @BotFather.")
        if ask_yn("Sudah punya bot Telegram dan punya token-nya?", False):
            pass
        else:
            print("\n  Cara buat bot baru:")
            print("  1. Buka app Telegram, search @BotFather")
            print("  2. Klik Start, ketik /newbot")
            print("  3. Beri nama bot (contoh: 'ARIA Personal Bot')")
            print("  4. Beri username (harus diakhiri _bot, contoh: aria_budi_bot)")
            print("  5. Copy token yang muncul (format: 1234567:ABC...)")
            if ask_yn("Buka @BotFather di Telegram?"):
                webbrowser.open("https://t.me/BotFather")
            input("\n  Setelah dapat token, tekan ENTER...")

    # Input token
    for attempt in range(3):
        token = ask("Bot Token dari @BotFather?", existing_token)
        if not token:
            continue
        bot_username = _test_telegram_token(token)
        if bot_username:
            ok(f"Token valid! Bot: @{bot_username}")
            break
        else:
            err(f"Token tidak valid (percobaan {attempt + 1}/3). Coba lagi.")
            existing_token = ""
    else:
        print("  Gagal validasi token. Lanjutkan setup dulu, isi token di .env nanti.")
        token = existing_token or ""
        bot_username = ""

    # Owner ID
    if not existing_owner:
        print("\n  Cara dapat User ID Telegram kamu:")
        print("  1. Search @userinfobot di Telegram")
        print("  2. Klik Start")
        print("  3. Dia langsung kasih angka ID kamu (contoh: 987654321)")
        if ask_yn("Buka @userinfobot di Telegram?"):
            webbrowser.open("https://t.me/userinfobot")
        input("  Setelah dapat angka ID, tekan ENTER...")

    owner_id = ask("User ID numerik kamu?", existing_owner)

    return {"bot_token": token, "owner_id": owner_id, "bot_username": bot_username}


def _test_telegram_token(token: str) -> str | None:
    try:
        url = f"https://api.telegram.org/bot{token}/getMe"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                return data["result"].get("username", "")
    except Exception:
        pass
    return None


# ── Step 4: Konfigurasi file ───────────────────────────────────────────────────

def step_configure(progress: dict, system_info: dict, identity: dict, telegram: dict) -> None:
    print("[4/7] Konfigurasi file")

    bot_name = identity["bot_name"]
    bot_slug = identity["bot_slug"]
    user_name = identity["user_name"]
    user_email = identity["user_email"]
    service_label = identity["service_label"]
    claude_path = system_info["claude_path"]
    bot_token = telegram["bot_token"]
    owner_id = telegram["owner_id"]

    # Tentukan log paths
    if OS == "Windows":
        log_dir = Path.home() / "AppData" / "Local" / bot_slug
        log_dir.mkdir(parents=True, exist_ok=True)
        bot_log = str(log_dir / f"{bot_slug}.log")
        bot_err_log = str(log_dir / f"{bot_slug}.error.log")
    else:
        bot_log = f"/tmp/{bot_slug}.log"
        bot_err_log = f"/tmp/{bot_slug}.error.log"

    # Tulis .env
    env_content = f"""# {bot_name} — Configuration (generated by setup.py)
# JANGAN commit file ini ke Git!

# --- Identitas ---
BOT_NAME={bot_name}
BOT_SLUG={bot_slug}
USER_NAME={user_name}
USER_EMAIL={user_email}

# --- Claude CLI ---
CLAUDE_CLI_PATH={claude_path}
CLAUDE_MODEL_DEFAULT=claude-sonnet-4-6

# --- Telegram ---
TELEGRAM_BOT_TOKEN={bot_token}
TELEGRAM_OWNER_ID={owner_id}

# --- Service ---
SERVICE_LABEL={service_label}
BOT_LOG={bot_log}
BOT_ERR_LOG={bot_err_log}

# --- Paths ---
MEMORY_DIR=./memory
DATA_DIR=./data

# --- App ---
LOG_LEVEL=INFO
"""
    env_file = WORKDIR / ".env"
    env_file.write_text(env_content)
    ok(".env dibuat")

    # CLAUDE.md dari template
    template_file = WORKDIR / "CLAUDE.md.template"
    claude_md_file = WORKDIR / "CLAUDE.md"
    if template_file.exists():
        content = template_file.read_text()
        content = content.replace("{{BOT_NAME}}", bot_name)
        content = content.replace("{{USER_NAME}}", user_name)
        content = content.replace("{{USER_EMAIL}}", user_email)
        claude_md_file.write_text(content)
        ok("CLAUDE.md dibuat dari template")

    # Buat folder
    for folder in ["memory/projects", "memory/tasks", "memory/notes", "memory/user", "data/conversations"]:
        (WORKDIR / folder).mkdir(parents=True, exist_ok=True)
    ok("Folder memory/ dan data/ disiapkan")

    # Buat venv
    if not VENV_DIR.exists():
        print("  Membuat virtual environment (.venv)...")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
        ok(".venv dibuat")
    else:
        ok(".venv sudah ada")

    # Install dependencies
    # PENTING: hindari "pip install ." karena setuptools akan memanggil setup.py
    # sebagai legacy build script → wizard berjalan rekursif → EOFError.
    # Solusi: baca deps dari pyproject.toml pakai tomllib, install langsung.
    pip = str(VENV_DIR / "bin" / "pip") if OS != "Windows" else str(VENV_DIR / "Scripts" / "pip.exe")
    print("  Menginstall dependencies (ini butuh 1-2 menit)...")
    run([pip, "install", "--upgrade", "pip", "--quiet"], check=False)

    import tomllib  # tersedia di Python 3.11+ stdlib
    pyproject_path = WORKDIR / "pyproject.toml"
    deps: list[str] = []
    if pyproject_path.exists():
        with open(pyproject_path, "rb") as _f:
            _data = tomllib.load(_f)
        deps = _data.get("project", {}).get("dependencies", [])

    if deps:
        run([pip, "install"] + deps + ["--quiet"])
        ok("Dependencies terinstall")
    else:
        err("Tidak ada dependencies di pyproject.toml — skip")

    # Tambah WORKDIR ke venv path supaya 'import el_solver' bisa jalan
    # tanpa harus pip install . (yang akan trigger setup.py)
    _site_pkgs_dirs = list((VENV_DIR / "lib").glob("python3.*"))
    if _site_pkgs_dirs:
        _pth = _site_pkgs_dirs[0] / "site-packages" / "sohibbot-local.pth"
        _pth.write_text(str(WORKDIR) + "\n")
        ok("Local package (el_solver) dikonfigurasi di venv")


# ── Step 5: Auto-restart service ───────────────────────────────────────────────

def step_service(progress: dict, identity: dict) -> None:
    print("[5/7] Auto-restart service")
    print("  Service ini akan memastikan bot nyala otomatis setelah laptop restart.")

    if not ask_yn("Setup auto-restart?"):
        print("  Skip service setup. Jalankan bot manual: scripts/start_bot.sh")
        return

    bot_slug = identity["bot_slug"]
    service_label = identity["service_label"]
    venv_python = str(VENV_DIR / "bin" / "python") if OS != "Windows" else str(VENV_DIR / "Scripts" / "python.exe")
    bot_script = str(WORKDIR / "scripts" / "claude_telegram.py")
    env_file = str(WORKDIR / ".env")

    if OS == "Darwin":
        _setup_launchd(service_label, bot_slug, venv_python, bot_script)
    elif OS == "Linux":
        _setup_systemd(bot_slug, venv_python, bot_script, env_file)
    else:
        _setup_windows_task(bot_slug, venv_python, bot_script)


def _setup_launchd(service_label: str, bot_slug: str, venv_python: str, bot_script: str) -> None:
    # Generate dari template kalau ada, atau buat langsung
    template = WORKDIR / "scripts" / "service" / "launchd.plist.template"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{service_label}.plist"

    bot_log = f"/tmp/{bot_slug}.log"
    bot_err_log = f"/tmp/{bot_slug}.error.log"

    if template.exists():
        content = template.read_text()
    else:
        content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{{SERVICE_LABEL}}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{{VENV_PYTHON}}</string>
        <string>{{BOT_SCRIPT}}</string>
    </array>
    <key>WorkingDirectory</key><string>{{WORKDIR}}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{{LOG_PATH}}</string>
    <key>StandardErrorPath</key><string>{{ERR_LOG_PATH}}</string>
</dict>
</plist>"""

    content = (content
               .replace("{{SERVICE_LABEL}}", service_label)
               .replace("{{VENV_PYTHON}}", venv_python)
               .replace("{{BOT_SCRIPT}}", bot_script)
               .replace("{{WORKDIR}}", str(WORKDIR))
               .replace("{{LOG_PATH}}", bot_log)
               .replace("{{ERR_LOG_PATH}}", bot_err_log))

    plist_path.write_text(content)
    ok(f"plist dibuat: {plist_path}")

    # Unload dulu kalau sudah ada
    run(["launchctl", "unload", str(plist_path)], check=False, capture=True)
    result = run(["launchctl", "load", str(plist_path)], check=False, capture=True)
    if result.returncode == 0:
        ok("Service di-load (launchd)")
    else:
        err(f"Gagal load service: {result.stderr}")
        print(f"  Manual: launchctl load {plist_path}")


def _setup_systemd(bot_slug: str, venv_python: str, bot_script: str, env_file: str) -> None:
    template = WORKDIR / "scripts" / "service" / "systemd.service.template"
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / f"{bot_slug}.service"

    bot_log = f"/tmp/{bot_slug}.log"
    bot_err_log = f"/tmp/{bot_slug}.error.log"

    if template.exists():
        content = template.read_text()
    else:
        content = """[Unit]
Description={{BOT_NAME}} Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory={{WORKDIR}}
ExecStart={{VENV_PYTHON}} {{BOT_SCRIPT}}
EnvironmentFile={{ENV_FILE}}
Restart=always
RestartSec=10
StandardOutput=append:{{LOG_PATH}}
StandardError=append:{{ERR_LOG_PATH}}

[Install]
WantedBy=default.target"""

    content = (content
               .replace("{{BOT_NAME}}", bot_slug)
               .replace("{{WORKDIR}}", str(WORKDIR))
               .replace("{{VENV_PYTHON}}", venv_python)
               .replace("{{BOT_SCRIPT}}", bot_script)
               .replace("{{ENV_FILE}}", env_file)
               .replace("{{LOG_PATH}}", bot_log)
               .replace("{{ERR_LOG_PATH}}", bot_err_log))

    service_path.write_text(content)
    ok(f"Service file dibuat: {service_path}")

    run(["systemctl", "--user", "daemon-reload"], check=False)
    run(["systemctl", "--user", "enable", "--now", f"{bot_slug}.service"], check=False)
    run(["loginctl", "enable-linger", os.environ.get("USER", "")], check=False)
    ok(f"Service {bot_slug}.service aktif")


def _setup_windows_task(bot_slug: str, venv_python: str, bot_script: str) -> None:
    template = WORKDIR / "scripts" / "service" / "windows_task.xml.template"
    task_xml = WORKDIR / "scripts" / "service" / "windows_task.xml"

    if template.exists():
        content = template.read_text(encoding="utf-16")
    else:
        content = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Settings>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
  </Settings>
  <Actions>
    <Exec>
      <Command>{{VENV_PYTHON}}</Command>
      <Arguments>{{BOT_SCRIPT}}</Arguments>
      <WorkingDirectory>{{WORKDIR}}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    content = (content
               .replace("{{VENV_PYTHON}}", venv_python)
               .replace("{{BOT_SCRIPT}}", bot_script)
               .replace("{{WORKDIR}}", str(WORKDIR)))

    task_xml.write_text(content, encoding="utf-16")
    ok(f"Task XML dibuat: {task_xml}")

    result = run(["schtasks", "/create", "/xml", str(task_xml), "/tn", bot_slug, "/f"], check=False, capture=True)
    if result.returncode == 0:
        run(["schtasks", "/run", "/tn", bot_slug], check=False)
        ok(f"Task Scheduler '{bot_slug}' aktif")
    else:
        err(f"Gagal buat Task Scheduler: {result.stderr}")
        print(f"  Manual: schtasks /create /xml {task_xml} /tn {bot_slug} /f")


# ── Step 6: Test pertama ───────────────────────────────────────────────────────

def step_test(telegram: dict) -> bool:
    print("[6/7] Test pertama")
    bot_username = telegram.get("bot_username", "")
    bot_name = f"@{bot_username}" if bot_username else "bot kamu"

    print(f"\n  Kirim pesan 'halo' ke {bot_name} di Telegram sekarang.")
    input("  Tekan ENTER setelah bot membalas (atau skip dengan ENTER langsung)...")

    return True


# ── Step 7: Agent pertama ──────────────────────────────────────────────────────

AGENT_OPTIONS = [
    ("web-researcher",  "Cari & rangkum info dari web"),
    ("content-creator", "Buat konten & caption media sosial"),
    ("email-writer",    "Draft email, proposal, & dokumen bisnis"),
    ("meeting-summary", "Rangkum rapat & transkrip audio/video"),
    ("daily-assistant", "Catatan harian, to-do, & reminder"),
    ("customer-reply",  "Template balas pesan & pertanyaan pelanggan"),
    ("custom",          "Custom — deskripsikan sendiri"),
    ("skip",            "Skip — bikin sendiri nanti"),
]


def step_first_agent(identity: dict) -> None:
    print("[7/7] Agent pertama kamu")
    print("\n  Agent adalah modul spesialis untuk task tertentu.")
    print("  Bot kamu bisa punya banyak agent sesuai kebutuhan.\n")
    print("  Apa yang paling sering mau kamu minta ke bot ini?")
    for i, (slug, desc) in enumerate(AGENT_OPTIONS, 1):
        print(f"  {i}. {desc}")

    choice_str = ask("\n  Pilihan (1-7)", "7")
    try:
        idx = int(choice_str) - 1
        if 0 <= idx < len(AGENT_OPTIONS):
            agent_slug, agent_desc = AGENT_OPTIONS[idx]
        else:
            agent_slug, agent_desc = "skip", "Skip"
    except ValueError:
        agent_slug, agent_desc = "skip", "Skip"

    if agent_slug == "skip":
        print("\n  Oke, skip. Bikin agent kapan saja dengan:")
        print(f"  python scripts/create_agent.py --type=basic --name=nama-agent")
        return

    if agent_slug == "custom":
        agent_slug = re.sub(r"[^a-z0-9]+", "-", ask("  Nama agent (kebab-case)?", "my-agent").lower()).strip("-")
        agent_desc = ask("  Deskripsi singkat agent ini?", "Asisten personal")

    print(f"\n  Membuat agent '{agent_slug}' dari template...")
    _create_agent(agent_slug, agent_desc)


def _create_agent(name: str, description: str, agent_type: str = "basic") -> None:
    agents_dir = WORKDIR / "agents"
    template_base = agents_dir / "_templates" / "_base"
    agent_dir = agents_dir / name

    if agent_dir.exists():
        ok(f"Agent '{name}' sudah ada di agents/{name}/")
        return

    agent_dir.mkdir(parents=True, exist_ok=True)

    # CLAUDE.md
    if (template_base / "CLAUDE.md.j2").exists():
        import jinja2  # noqa: F401 — hanya dipakai jika template tersedia
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(template_base)))
        tmpl = env.get_template("CLAUDE.md.j2")
        content = tmpl.render(agent_name=name, description=description)
    else:
        content = f"# {name}\n\n{description}\n\nAgent ini dibuat via setup.py.\n"
    (agent_dir / "CLAUDE.md").write_text(content)

    # manifest.yaml
    manifest = f"""name: {name}
description: {description}
version: "1.0"
type: basic
"""
    (agent_dir / "manifest.yaml").write_text(manifest)

    # run.py
    run_py = f"""#!/usr/bin/env python3
\"\"\"Agent: {name} — {description}\"\"\"
import sys
import subprocess
from pathlib import Path

WORKDIR = Path(__file__).parent.parent.parent
CLAUDE = WORKDIR / ".venv" / "bin" / "python"


def main(prompt: str) -> None:
    result = subprocess.run(
        ["claude", "--print", "--dangerously-skip-permissions", prompt],
        cwd=str(WORKDIR),
        capture_output=True,
        text=True,
    )
    print(result.stdout or result.stderr)


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) if len(sys.argv) > 1 else "halo, perkenalkan dirimu")
"""
    (agent_dir / "run.py").write_text(run_py)

    # memory dir
    (agent_dir / "memory").mkdir(exist_ok=True)

    ok(f"Scaffold dibuat: agents/{name}/")
    if ask_yn("  Buka panduan agent di browser?"):
        webbrowser.open("https://github.com/wildanrivky/sohibbot/blob/main/agents/_templates/README.md")


# ── Final summary ──────────────────────────────────────────────────────────────

def print_summary(identity: dict, telegram: dict) -> None:
    bot_name = identity["bot_name"]
    bot_slug = identity["bot_slug"]
    service_label = identity["service_label"]
    bot_username = telegram.get("bot_username", "")

    banner(f"""SETUP SELESAI!

Bot kamu: {bot_name}{f' (@{bot_username})' if bot_username else ''}
Status: Active

Perintah berguna:""")

    if OS == "Darwin":
        print(f"  Stop bot:    launchctl unload ~/Library/LaunchAgents/{service_label}.plist")
        print(f"  Restart:     launchctl kickstart -k gui/$(id -u)/{service_label}")
        print(f"  Lihat log:   tail -f /tmp/{bot_slug}.error.log")
    elif OS == "Linux":
        print(f"  Stop bot:    systemctl --user stop {bot_slug}.service")
        print(f"  Restart:     systemctl --user restart {bot_slug}.service")
        print(f"  Lihat log:   tail -f /tmp/{bot_slug}.error.log")
    else:
        print(f"  Stop bot:    schtasks /end /tn {bot_slug}")
        print(f"  Restart:     schtasks /run /tn {bot_slug}")
        print(f"  Lihat log:   lihat di AppData\\Local\\{bot_slug}\\")

    print(f"\n  Coba kirim 'halo' ke bot di Telegram.")
    print(f"  Panduan lengkap: baca ONBOARDING.md\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    banner("""SELAMAT DATANG DI SOHIBBOT SETUP
Bot AI pribadi kamu — otak Claude Code,
jalan di laptopmu sendiri""")

    # Check if resuming
    progress = load_progress()
    if progress and progress.get("completed"):
        if not ask_yn("Setup sudah pernah dijalankan. Jalankan ulang?", False):
            print_summary(progress, progress)
            return

    try:
        system_info = step_check_system()
        save_progress(system_info)
        print()

        identity = step_identity(progress | system_info)
        save_progress(identity)
        print()

        telegram = step_telegram(progress | identity)
        save_progress(telegram)
        print()

        step_configure(progress | system_info | identity | telegram, system_info, identity, telegram)
        print()

        step_service(progress | identity, identity)
        print()

        # Tunggu sebentar supaya service sempat start
        time.sleep(3)

        step_test(telegram)
        print()

        step_first_agent(identity)
        print()

        save_progress({"completed": True})
        print_summary(identity, telegram)

    except KeyboardInterrupt:
        print("\n\nSetup dihentikan. Data yang sudah diisi tersimpan.")
        print("Jalankan ulang 'python setup.py' untuk lanjutkan.")


if __name__ == "__main__":
    main()
