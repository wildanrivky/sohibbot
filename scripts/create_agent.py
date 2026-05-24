#!/usr/bin/env python3
"""
Scaffold agent baru dari template.

Penggunaan:
  python scripts/create_agent.py --name=nama-agent --type=basic
  python scripts/create_agent.py --name=nama-agent --type=basic --desc="Deskripsi agent"
"""
import argparse
import re
import sys
from pathlib import Path

WORKDIR = Path(__file__).parent.parent.resolve()
AGENTS_DIR = WORKDIR / "agents"
TEMPLATES_DIR = AGENTS_DIR / "_templates"

VALID_TYPES = ["basic", "conversational", "pipeline", "reactive", "scheduled"]


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def create_agent(name: str, description: str, agent_type: str) -> None:
    agent_dir = AGENTS_DIR / name

    if agent_dir.exists():
        print(f"Agent '{name}' sudah ada di agents/{name}/")
        sys.exit(0)

    agent_dir.mkdir(parents=True)

    # Coba pakai template Jinja2 dari _templates/
    template_base = TEMPLATES_DIR / "_base"
    archetype_file = TEMPLATES_DIR / agent_type / "archetype.j2"

    try:
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(WORKDIR)))
        ctx = {"agent_name": name, "description": description, "agent_type": agent_type}

        # CLAUDE.md
        if (template_base / "CLAUDE.md.j2").exists():
            tmpl = env.get_template(f"agents/_templates/_base/CLAUDE.md.j2")
            (agent_dir / "CLAUDE.md").write_text(tmpl.render(**ctx))
        else:
            _write_basic_claude_md(agent_dir, name, description)

        # run.py
        if (template_base / "run.py.j2").exists():
            tmpl = env.get_template(f"agents/_templates/_base/run.py.j2")
            (agent_dir / "run.py").write_text(tmpl.render(**ctx))
        else:
            _write_basic_run_py(agent_dir, name, description)

        # manifest.yaml
        if (template_base / "manifest.yaml.j2").exists():
            tmpl = env.get_template(f"agents/_templates/_base/manifest.yaml.j2")
            (agent_dir / "manifest.yaml").write_text(tmpl.render(**ctx))
        else:
            _write_basic_manifest(agent_dir, name, description, agent_type)

    except ImportError:
        # Jinja2 belum ada — pakai plaintext fallback
        _write_basic_claude_md(agent_dir, name, description)
        _write_basic_run_py(agent_dir, name, description)
        _write_basic_manifest(agent_dir, name, description, agent_type)

    # memory dir
    (agent_dir / "memory").mkdir(exist_ok=True)
    (agent_dir / "memory" / ".gitkeep").touch()

    print(f"Agent '{name}' berhasil dibuat di agents/{name}/")
    print(f"  CLAUDE.md    — instruksi untuk agent ini")
    print(f"  run.py       — entry point")
    print(f"  manifest.yaml — metadata")
    print(f"  memory/      — long-term memory agent")
    print(f"\nCara pakai:")
    print(f"  python agents/{name}/run.py 'pertanyaan atau task kamu'")


def _write_basic_claude_md(agent_dir: Path, name: str, desc: str) -> None:
    (agent_dir / "CLAUDE.md").write_text(f"""# {name}

{desc}

## Cara kerja

Agent ini menerima input dari user dan mengolahnya dengan Claude Code CLI.

## Panduan

- Baca konteks dari memory/ kalau relevan
- Simpan catatan penting ke memory/
- Jawab langsung ke poin
""")


def _write_basic_run_py(agent_dir: Path, name: str, desc: str) -> None:
    content = f"""#!/usr/bin/env python3
\"\"\"Agent: {name} — {desc}\"\"\"
import sys
import subprocess
from pathlib import Path

WORKDIR = Path(__file__).parent.parent.parent
CLAUDE_MD = Path(__file__).parent / "CLAUDE.md"


def run(prompt: str) -> str:
    system_prompt = CLAUDE_MD.read_text() if CLAUDE_MD.exists() else ""
    full_prompt = f"{{system_prompt}}\\n\\n{{prompt}}" if system_prompt else prompt
    result = subprocess.run(
        ["claude", "--print", "--dangerously-skip-permissions", full_prompt],
        cwd=str(WORKDIR),
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or result.stderr.strip()


def main() -> None:
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "halo, perkenalkan dirimu"
    print(run(prompt))


if __name__ == "__main__":
    main()
"""
    (agent_dir / "run.py").write_text(content)


def _write_basic_manifest(agent_dir: Path, name: str, desc: str, agent_type: str) -> None:
    (agent_dir / "manifest.yaml").write_text(f"""name: {name}
description: {desc}
version: "1.0"
type: {agent_type}
""")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold agent baru dari template SohibBot")
    parser.add_argument("--name", required=True, help="Nama agent (akan jadi nama folder, kebab-case)")
    parser.add_argument("--type", default="basic", choices=VALID_TYPES, help="Tipe agent")
    parser.add_argument("--desc", default="Agent personal", help="Deskripsi singkat agent")
    args = parser.parse_args()

    name = slugify(args.name)
    if not name:
        print("Nama agent tidak valid.")
        sys.exit(1)

    create_agent(name, args.desc, args.type)


if __name__ == "__main__":
    main()
