#!/usr/bin/env bash
set -euo pipefail

: "${REPORT_DIR:?Set REPORT_DIR to an extracted interactive report directory}"

SESSION="${REPORT_SESSION_NAME:-gpic-report-quote-free}"
HOST="${REPORT_HOST:-0.0.0.0}"
PORT="${REPORT_PORT:-8771}"
USER_NAME="${REPORT_USER:-gpic}"
PASSWORD="${REPORT_PASSWORD:-1234}"

if [[ ! -f "$REPORT_DIR/report_server.py" || ! -f "$REPORT_DIR/report.db" ]]; then
  echo "REPORT_DIR is not a complete interactive report: $REPORT_DIR" >&2
  exit 2
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
fi

tmux new-session -d -s "$SESSION" \
  "cd '$REPORT_DIR' && REPORT_HOST='$HOST' REPORT_PORT='$PORT' REPORT_USER='$USER_NAME' REPORT_PASSWORD='$PASSWORD' REPORT_OPEN_BROWSER=0 python3 report_server.py"

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null; then
    echo "report ready: http://${HOST}:${PORT}/viewer.html"
    tmux capture-pane -pt "$SESSION" -S -20
    exit 0
  fi
  sleep 1
done

echo "report server did not become ready within 20 seconds" >&2
tmux capture-pane -pt "$SESSION" -S -50 >&2 || true
exit 1
