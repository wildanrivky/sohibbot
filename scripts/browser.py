#!/usr/bin/env python
"""
CLI wrapper untuk browser automation. Dipanggil Claude via Bash tool.

Pemakaian:
  python scripts/browser.py youtube "nama lagu atau artis"
  python scripts/browser.py open "https://..."
"""
from __future__ import annotations

import json
import sys

# Pastikan package el_solver bisa diimport dari direktori manapun
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from el_solver.skills.browser import open_url, youtube_play


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: browser.py <command> <argument>", file=sys.stderr)
        print("Commands: youtube, open", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    arg = " ".join(sys.argv[2:])

    if cmd == "youtube":
        result = youtube_play(arg)
        print(json.dumps(result, ensure_ascii=False))

    elif cmd == "open":
        open_url(arg)
        print(json.dumps({"url": arg, "status": "opened"}))

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
