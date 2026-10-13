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

## Bring your own EPANET network (upload a `.inp`)
Build the water network in EPANET itself and export its `.inp`, then **upload it**
in the builder (or `POST /import_inp`). It is parsed into drawable elements (with
coordinates) and the raw file is kept verbatim; the lab then **runs on that
authoritative `.inp`**, not a regenerated one. You only add PLCs and assign each
element to one (register binding). `DesignApp` serves any such design over the
Integration API (the external program a CORE/networked run connects to).

## The run engine is real EPANET
A lab config is **EPANET-backed**: `make_lab(n)` builds a real physical network
(a reservoir plus one tank/pump loop per PLC under `config["epanet"]`), the
control `rules`, and each PLC's **register bindings** to EPANET elements (a level
register ← a tank's level; a command register → a pump's status). `/run` and the
live sessions use `wds_lab.WdsLab`, which drives the EPANET library: it reads the
tank level from EPANET → the control engine decides → it writes the pump command
back into EPANET → advances the hydraulic step. The browser-only preview (no
server) is an approximation and is labelled as such; the server runs real EPANET.

## One-command orchestrator (`launch`)
```bash
./run.sh launch lab.json --cycles 60 --attack dos:PLC1:20:40 -o lab_run
```
`CoreLauncher` prepares the whole **CORE bring-up package** (`core_session.py`,
`run_devices.sh`, gateway INIs, `lab.json`, and an auto-generated `launch.sh`),
runs the full pipeline through the networked engine, and writes a reproducible
**experiment bundle** (`trace.csv` + two-dimensional `labels.csv` +
`metadata.json`; pcaps are added on a CORE host). It reports whether CORE is
available here; on a CORE host, `bash lab_run/launch.sh` brings the lab up inside
emulated namespaces (see the runbook). The `app` command serves the external
EPANET application that such a run connects to:
```bash
./run.sh app lab.json --port 9000
```

## Two run engines
- **`/run`** — fast EPANET loop in one process (quick preview of the physics + control).
- **`/netrun`** (and `cli netrun`) — the **full networked run**: the external
  EPANET app, the field-device nodes (Modbus + I/O-modules back channel), the
  gateway and an MTU control loop, all wired over **real sockets** — the exact
  CORE node software. On a CORE host the same processes run inside nodes on
  emulated links (netem + pcap, see the runbook); here they run over loopback.
  ```bash
  ./run.sh netrun lab.json --cycles 60 --attack dos:PLC1:20:40
  ```

## Attack model
On the shared timeline: `dos`/`delay` **freeze** a PLC's command (level over/under-
shoots) and mark the attack window; `integrity` **forces the command off** and
marks samples **confirmed-malicious**. Window and confirmed are kept as separate
label dimensions, matching the dataset design.

> The preview runs the real `RuleEngine` over a lightweight per-PLC process so it
> is instant and CORE-free. For emulated-network experiments with real traffic
> and pcap capture, generate the session (`/generate`) and follow the runbook.
