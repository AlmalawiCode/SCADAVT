#!/usr/bin/env bash
# Thin wrapper for the SCADAVT CLI — sets the environment so you don't retype it.
#   ./scadavt.sh scaffold --plcs 3 -o /tmp/net.json
#   ./scadavt.sh validate /tmp/net.json
#   ./scadavt.sh run /tmp/net.json --live        # needs the CORE daemon
# Live verbs (run / plan with CORE) use the CORE Python; the rest use .venv.
cd "$(dirname "$0")"
export SCADAVT_DIR="$PWD"
export SCADAVT_NODE_PY="$PWD/.venv/bin/python"
PY="$PWD/.venv/bin/python"
case "$1" in run) PY="/opt/core/venv/bin/python" ;; esac
for a in "$@"; do [ "$a" = "--live" ] && PY="/opt/core/venv/bin/python"; done
exec "$PY" -m scadavt "$@"
