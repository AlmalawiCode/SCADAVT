#!/bin/sh
# all     : tests + experiments + figures (default)
# test    : unit tests only
# suite   : experiments only
# figures : figures and tables from existing results
set -e
cd /app
if [ ! -s data/consumption_per_person.csv ] && [ "$1" != "test" ]; then
  echo "Downloading Melbourne Water weekly use per person..."
  python tools/fetch_consumption.py --out data/consumption_per_person.csv
fi
case "$1" in
  test)    python -m pytest -q tests ;;
  suite)   python -m scadavt.experiments.suite --out /out/results ;;
  figures) python -m scadavt.analysis.make_figures --results /out/results --out /out/paper
           python -m scadavt.analysis.diagrams --out /out/paper/figures ;;
  all)     python -m pytest -q tests
           python -m scadavt.experiments.suite --out /out/results
           python -m scadavt.analysis.make_figures --results /out/results --out /out/paper
           python -m scadavt.analysis.diagrams --out /out/paper/figures ;;
  *)       exec "$@" ;;
esac
