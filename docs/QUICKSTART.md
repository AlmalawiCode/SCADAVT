# SCADAVT Research Lab — Quick Start

A browser-based laboratory for designing SCADA networks, simulating the water
plant and its communications, running controlled cyberattacks, monitoring their
effect, and exporting labelled, reproducible IDS datasets. It is built on the
validated SCADAVT 2.0 simulation core; the web layer never alters the physics or
the protocol behaviour (see `docs/RESEARCH_LAB_DESIGN.md`).

## 1. Run it with Docker (recommended, one command)

From the repository root:

```bash
docker compose -f docker-compose.lab.yml up --build
```

Then open <http://127.0.0.1:8000>.

- The first run downloads the Melbourne Water consumption series into `./data`
  (needed by the demand model; not redistributed with the code). If the machine
  is offline, place a `consumption_per_person.csv` in `./data` beforehand
  (`python tools/fetch_consumption.py`).
- Each run's ground-truth log is written under `./runs/<timestamp>/`.

To stop: `Ctrl-C`, or `docker compose -f docker-compose.lab.yml down`.

## 2. Run it without Docker

```bash
pip install -e ".[webui]"
python -m scadavt.webui            # http://127.0.0.1:8000
# or, after install:  scadavt-lab
```

## 3. Using the lab (Phase 1)

1. The canvas shows the real network of the loaded scenario (the `town` case
   study): 6 tanks, 12 pumps, valves, and the pipe graph, drawn from the EPANET
   model's coordinates.
2. Set the run length, random seed and simulation speed, then press **Start**.
   The lab launches the plant server and drives it with the validated
   supervisory logic in real time.
3. Watch tanks fill and drain, pumps switch, and V1 balance flow live; the tank
   chart tracks levels against the critical threshold.
4. Click any tank, pump or valve to see its live values and which PLC and tags
   supervise it.
5. Press **Stop** to end the run.

This is the **process-level live mode** (no containers), scientifically
equivalent to `python -m scadavt.experiments.offline` but real-time.

## 4. Running a controlled attack (Phase 2)

Attacks are declared in a normal networked experiment file under an `attacks:`
list and run on the containerised deployment. Example
(`experiments/a1_fdi_t2.yaml`): a False Data Injection that makes the MTU read a
constant falsified T2 level while the true tank diverges.

```bash
# builds the images once, then runs the 51-container town + an isolated attacker
docker build -t scadavt:2.0 . && docker build -t scadavt-net:2.0 -f deploy/Dockerfile.net .
python -m scadavt.attacks.runner experiments/a1_fdi_t2.yaml
```

Results land in `results/attacks/<name>/`: `ground_truth.csv` (true plant),
`historian.csv` (what the MTU saw — falsified in-window), `faults.csv` (the
attack labels with wall and simulated time), and `attacks/<id>.csv` (attacker
actions). Each attacker is confined to one field network and cannot reach the
plant's ground-truth back channel or the Internet.

FDI modes: `const` (report a fixed value), `offset` (true + value), `freeze`
(hold the first true value seen).

## 5. What is coming

| Phase | Capability |
|---|---|
| P3 | Replay, command manipulation, DoS; automatic labelling; reproducible dataset export |
| P4 | Drag-and-drop network authoring with live hydraulic validity checks; attack panel in the GUI |

## 6. Tests

```bash
python -m pytest -q tests/test_webui.py tests/test_attacks.py
# end-to-end MITM test (needs Docker + the scadavt-net:2.0 image):
SCADAVT_DOCKER_TESTS=1 python -m pytest -q tests/test_attacks.py::test_mitm_end_to_end
```
