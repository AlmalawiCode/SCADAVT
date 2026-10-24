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
- Select a node to edit `id` / `ip` / `seed`.
- **Rules** box, one per line: `S1 < 3 => A2 open` (PLC1's low sensor opens
  PLC2's actuator) — arbitrary cross-PLC control relationships.
- **Export** downloads the canonical config JSON; **Import** loads one back
  (Python-authored or GUI-authored) — lossless round-trip.
- **Validate** / **Run** call the service (`/validate`, `/run`).

## Interchangeability (R3/R8)

The exported file is an ordinary canonical config: author it in Python, open it
in the designer, edit, export, and run it from the CLI — nothing is lost, because
there is one model and one file format. A config built entirely in Python
(`corelive.grid(...)` or hand-written) opens unchanged in the designer, and a
config built in the designer runs unchanged via `scadavt run` and `corelive`.

## Verification

- Unit/contract (`tests/test_designer.py`): served same-origin, domain-neutral,
  lossless round-trip incl. a GUI-style edit that still validates and plans,
  arbitrary non-grid topology valid + planned.
- Real-browser E2E (`tests/e2e_designer.js`, needs `node` + `puppeteer-core` +
  Chrome): loads the served designer, builds an arbitrary topology + a cross-PLC
  rule through the GUI's own code, exports the canonical config, and validates it
  via the service — verified: 4 nodes / 3 links / 1 rule, validate ok, no page
  errors.
- Live execution of a GUI-shaped (arbitrary, non-grid) config on real CORE via
  the identical engine the service `/run` uses: `scadavt run --live` → MTU
  reached every device (3/3).

## Note

A single interpreter holding both FastAPI and the CORE libraries was not
available in the dev sandbox (`/opt/core` venv is root-owned), so the browser
E2E exercised create/export/validate; live `/run` from the browser works where
`pip install .[webui]` is run in the CORE environment. The `/run` engine path is
identical to the verified `scadavt run --live`.
