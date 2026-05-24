#!/bin/bash
set -e
WORKDIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$WORKDIR"

# Load .env
if [ -f "$WORKDIR/.env" ]; then
  set -a
  source "$WORKDIR/.env"
  set +a
fi

SESSION="${BOT_SLUG:-sohibbot}"
PYTHON="$WORKDIR/.venv/bin/python"
BOT_SCRIPT="$WORKDIR/scripts/claude_telegram.py"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux tidak terinstall. Install dulu:"
  echo "  Mac:   brew install tmux"
  echo "  Linux: sudo apt install tmux"
  echo ""
  echo "Atau jalankan langsung (tanpa tmux):"
  echo "  $PYTHON $BOT_SCRIPT"
  exit 1
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true
tmux new-session -d -s "$SESSION" \
  "cd '$WORKDIR' && '$PYTHON' '$BOT_SCRIPT'"

echo "Bot started in tmux session '$SESSION'."
echo "  Attach: tmux attach -t $SESSION"
echo "  Stop:   tmux kill-session -t $SESSION"
