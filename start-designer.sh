#!/usr/bin/env bash
# Start the SCADAVT network designer and open it in your browser.
#
#   ./start-designer.sh            # uses port 8142
#   ./start-designer.sh 9000       # pick a port
#
# Leave it running; press Ctrl-C to stop. For the live "Run" button you need the
# CORE daemon up:  sudo systemctl start core-daemon
set -euo pipefail
cd "$(dirname "$0")"                      # the repo folder (this script lives in it)

PORT="${1:-8142}"
VENV_PY="$PWD/.venv/bin/python"
CORE_PY="/opt/core/venv/bin/python"       # the Python that can drive CORE (live Run)
URL="http://127.0.0.1:$PORT/designer/"

[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3)"

export SCADAVT_DIR="$PWD"
export SCADAVT_NODE_PY="$VENV_PY"
[ -x "$CORE_PY" ] && export SCADAVT_CORE_PYTHON="$CORE_PY"

echo "SCADAVT designer:  $URL"
echo "(Ctrl-C to stop)"

# open the browser once the server is listening
( for _ in $(seq 1 40); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then break; fi
    sleep 0.25
  done
  (xdg-open "$URL" >/dev/null 2>&1 || open "$URL" >/dev/null 2>&1 || true) ) &

exec "$VENV_PY" -m scadavt.service --host 127.0.0.1 --port "$PORT"
