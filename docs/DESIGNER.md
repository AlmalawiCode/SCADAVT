# Generic graphical designer (Phase 2, M6)

An **optional** web designer for building SCADAVT networks visually. It is a thin
presentation layer: its in-memory document *is* the canonical config
(`scadavt.core.model`), and it performs no validation or execution itself — it
calls the shared HTTP service (`scadavt.service`), which wraps the same L1/L2 the
Python API/CLI use. Domain-neutral (no water/EPANET).

## Run it

```bash
# needs the optional web deps in the same environment as CORE for live runs:
pip install .[webui]
# start the service (serves the designer same-origin); local-only by default
python -m scadavt.service                 # http://127.0.0.1:8099
# open the designer
xdg-open http://127.0.0.1:8099/designer/
```

Remote access requires a token (never exposed unauthenticated):

```bash
SCADAVT_TOKEN=secret python -m scadavt.service --host 0.0.0.0
# in the designer, set the Auth token field to `secret`
```

## Use it

- **+PLC / +MTU / +Switch / +Router** add nodes; drag to place.
- **Link mode** on, then click two nodes to wire them (arbitrary topology).
- Select a node to edit `id` / `ip`. A selected **PLC** also shows its **I/O
  modules** (a new PLC has `AI×4`, `DI×4`, `DO×4`; add/remove with **+ Module** /
  **✕**) and a **channel table** with each channel's Modbus address (`AI0 30001`,
  `DI0 10001`, `DO0 00001`, …), computed by the service (`POST /io`).
- **Wired to** on a channel attaches an **emulated sensor** (temperature,
  humidity, pressure, level, flow, analog on AI; switch on DI) or **actuator**
  (pump, fan, valve, lamp, motor on DO; valve_position, speed on AO); a sensor's
  signal is `constant`/`sine`/`ramp`/`random`/`square`. Types come from
  `GET /io/catalog`. See `scadavt.core.fieldio`.
- **Rules** box, one per line: `PLC1.AI0 > 25 => PLC2.DO0 on` (operators
  `< > <= >= == !=`; action `on`/`off` or a number) — arbitrary cross-PLC
  control relationships.
- **Export** downloads the canonical config JSON; **Import** loads one back
  (Python-authored or GUI-authored) — lossless round-trip.
- **Validate** / **Run** call the service (`/validate`, `/run`).

## Interchangeability (R3/R8)

The exported file is an ordinary canonical config: author it in Python, open it
in the designer, edit, export, and run it from the CLI — nothing is lost, because
there is one model and one file format. A config built entirely in Python
(`corelive.grid(...)` or hand-written) opens unchanged in the designer, and a
config built in the designer runs unchanged via `scadavt run` and `corelive`.

## How `/run` executes (one engine, two deployments)

`/run` always goes through the single shared runner
`scadavt.core.corelive.run_live`, which the CLI (`scadavt run`) uses too — so
both interfaces produce the same result shape.

- **in-process** — where `pip install .[webui]` is done in the CORE environment,
  the service imports the CORE API and runs directly;
- **delegated** — set `SCADAVT_CORE_PYTHON=/path/to/core/python` and the service
  runs `<it> -m scadavt run <cfg> --live --json` as a subprocess (outbound gRPC
  to the daemon). This lets the HTTP service run on an interpreter that cannot
  itself load CORE (e.g. when only an out-of-process interpreter can reach CORE).

Without either, `/run` returns 503 — the rest of the service (and all of the
Python CLI) keeps working.

## Verification

- Unit/contract (`tests/test_designer.py`, `tests/test_service.py`): served
  same-origin, domain-neutral, lossless round-trip incl. a GUI-style edit that
  still validates and plans, arbitrary non-grid topology valid + planned, and
  `/run` delegation (fake CORE interpreter) returns results.
- **Genuine browser → CORE E2E** (`tests/e2e_designer_run.js`, needs `node` +
  `puppeteer-core` + Chrome, plus a service with CORE in-process or via
  `SCADAVT_CORE_PYTHON`): builds an arbitrary 3-PLC topology through the GUI's
  own code, wires an emulated temperature sensor (constant 28) to each PLC's
  `AI0`, adds a cross-PLC rule ring (`PLCn.AI0 > 25 => PLCn+1.DO0 on`),
  validates, and **executes it live on CORE via `/run`**: `reached 3/3`, every
  `AI0 = 28`, every `DO0 = 1` (the ring fired), no page errors.
- **Real-click I/O E2E** (`tests/e2e_designer_io.js`): toolbar clicks, Connect
  mode, the channel table's *Wired to* lists, typing a sensor's value, Apply
  rules, Validate, ▶ Run — the tutorial's worked example step by step, with
  10 assertions on the live result.
- **Consistency**: the same GUI-exported config run through the Python CLI
  (`scadavt run --live`) gives identical results (3/3, every `AI0 = 28.00`,
  every `DO0 = 1.00`) — one engine behind both interfaces.
- A simpler create/export/validate browser E2E is `tests/e2e_designer.js`.
