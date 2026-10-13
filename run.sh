#!/usr/bin/env bash
# SCADAVT lab launcher.
#   ./run.sh                      -> web builder + API at http://127.0.0.1:8000
#   ./run.sh make --plcs 3 -o lab.json
#   ./run.sh run lab.json --cycles 120 --attack integrity:PLC2:40:90
#   ./run.sh serve --port 8077
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH=.
PY="${PYTHON:-.venv/bin/python}"
if [ "$#" -eq 0 ]; then
  echo "SCADAVT lab builder -> http://127.0.0.1:8000  (Ctrl+C to stop)"
  exec "$PY" -m scadavt.labui
else
  exec "$PY" -m scadavt.labui.cli "$@"
fi
