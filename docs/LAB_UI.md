# Lab UI + API (`scadavt.labui`)

One clean API drives everything; the drag-and-drop web builder and the terminal
client are both just clients of it. Build a lab, run it, and attack it — from the
browser or the terminal — with no CORE needed (preview). Real networked runs use
the core modules on a CORE host (`docs/CORE_RUNBOOK.md`).

## Start the builder (web)
```bash
cd scadavt && PYTHONPATH=. .venv/bin/python -m scadavt.labui        # http://127.0.0.1:8000
```
Open the page: choose the number of PLCs, drag the nodes to arrange the diagram,
set each node's properties, optionally pick an attack, then **Run** to watch the
levels live. The API docs are at `/docs`.

## API (used by the UI and the CLI)
```
POST /validate  {config}                 -> {issues}
POST /generate  {config}                 -> {files}            # CORE session + gateway files
POST /run       {config, cycles, attacks}-> {traces, summary}  # one-shot preview
POST /sessions  {config}                 -> {id}               # live session
POST /sessions/{id}/attack {kind,target,start?,duration?}      # schedule OR inject now
POST /sessions/{id}/step   {n}           -> {t, traces, summary}
GET  /sessions/{id}                      -> {t, traces, summary}
POST /sessions/{id}/stop
```

## Terminal (`scadavt.labui.cli`)
```bash
# build + inspect
python -m scadavt.labui.cli make --plcs 4 --no-mtu -o lab.json
python -m scadavt.labui.cli validate lab.json
python -m scadavt.labui.cli generate lab.json -o out/

# run locally and schedule attacks  (kind:target:start:end)
python -m scadavt.labui.cli run lab.json --cycles 120 \
    --attack dos:PLC1:30:70 --attack integrity:PLC2:50:90 --out trace.csv

# live: inject an attack mid-run against a running server (scripts can do this)
python -m scadavt.labui.cli session new lab.json          # -> {id}
python -m scadavt.labui.cli session attack <id> --kind dos --target PLC1
python -m scadavt.labui.cli session step   <id> --n 40
```

## Attack model (preview)
On the shared timeline: `dos`/`delay` **freeze** a PLC's command (level over/under-
shoots) and mark the attack window; `integrity` **forces the command off** and
marks samples **confirmed-malicious**. Window and confirmed are kept as separate
label dimensions, matching the dataset design.

> The preview runs the real `RuleEngine` over a lightweight per-PLC process so it
> is instant and CORE-free. For emulated-network experiments with real traffic
> and pcap capture, generate the session (`/generate`) and follow the runbook.
