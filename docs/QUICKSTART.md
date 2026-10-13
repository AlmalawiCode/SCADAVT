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

## 4. Running a controlled attack — from the browser (Phase 2–4)

Build the testbed images once, then run the lab backend on the host so it can
orchestrate the containers:

```bash
docker build -t scadavt:2.0 . && docker build -t scadavt-net:2.0 -f deploy/Dockerfile.net .
pip install -e ".[webui]" && scadavt-lab          # open http://127.0.0.1:8000
```

Then, entirely in the browser (no commands):

1. In **Attack builder**, click a component to prefill the target (or type a
   PLC/tag), choose a kind and window, and **Add attack**.
2. **Run on testbed** — the lab launches the 51-container town plus an isolated
   attacker and streams a **live chart** of the **true** value (blue) vs. what
   the **MTU sees** (red, falsified while the attack window is shaded), with a
   progress bar and an "ATTACK ACTIVE" indicator. (If the images are not built
   yet, a banner offers a one-click **Build images**; if Docker is absent it
   says so — the viewer, authoring and builder still work.)
3. When it finishes, **Export dataset** downloads the labelled bundle.
4. **Saved runs** lists every run; **↻ reproduce** re-runs one from its saved
   files (deterministic for the same seed).

The same thing from the command line (the GUI drives exactly this):

```bash
python -m scadavt.attacks.runner experiments/a1_fdi_t2.yaml
```

Results land in `results/attacks/<name>/` (or `runs/attacks/<name>/out/` for GUI
runs): `ground_truth.csv` (true plant), `historian.csv` (what the MTU saw —
falsified in-window), `faults.csv` (attack labels with wall and simulated time),
and `attacks/<id>.csv` (attacker actions). Each attacker is confined to one
field network and cannot reach the plant's ground-truth back channel or the
Internet.

> **FDI needs a polled sensor.** The MTU reads tank levels and a few flows, so
> FDI/replay target those (e.g. `PLC1.T2_LVL`). An actuator node such as PLC6
> (the V1 valve) is not polled by the MTU — its matching attack is `command`
> (altering the setpoint the MTU writes). The attack builder validates this for
> you.

Attack kinds (`experiments/a1…a4`):

| kind | target | effect |
|---|---|---|
| `fdi` | a float sensor (`'i'`) | falsify the reading the MTU sees (`const`/`offset`/`freeze`) |
| `replay` | a float sensor | replay recorded legitimate readings while the plant diverges |
| `command` | an actuator (`'o'`) | rewrite the MTU's command on the wire (e.g. close a valve) |
| `dos` | a PLC | 100 % packet loss on its field link — loss of monitoring/control |

## 5. Building a labelled, reproducible dataset (Phase 3)

```bash
python -m scadavt.attacks.runner  experiments/a1_fdi_t2.yaml
python -m scadavt.attacks.dataset --result results/attacks/a1_fdi_t2 --out datasets/a1_fdi_t2
```

The bundle contains `process.csv` (ground truth + MTU-observed values),
`network.csv` (per-cycle connection/gateway features), `labels.csv` (binary +
multiclass, aligned by `sim_h`), and `manifest.json` — which pins the seed,
scenario/EPANET SHA-256, Docker image digest and git commit so the dataset
reproduces deterministically.

## 6. Authoring and the attack builder (Phase 4)

In the browser:

- **Layout authoring** — tick *Edit layout* and drag tanks, junctions and
  reservoirs to reposition them. **Save layout** writes the coordinates to a
  private workspace copy of the scenario and checks it with a real EPANET solve;
  an edit that would break the hydraulics is rejected, not saved. The packaged
  scenario is never modified.
- **Attack builder** — click a component to prefill the target, pick a kind and
  window, **Add attack** (validated live against the scenario), then **Export
  experiment** to download a ready-to-run `experiment.yaml`:

  ```bash
  python -m scadavt.attacks.runner <downloaded>.yaml
  python -m scadavt.attacks.dataset --result results/attacks/<name> --out datasets/<name>
  ```

## 7. Tests

```bash
python -m pytest -q tests/test_webui.py tests/test_attacks.py
# end-to-end MITM test (needs Docker + the scadavt-net:2.0 image):
SCADAVT_DOCKER_TESTS=1 python -m pytest -q tests/test_attacks.py::test_mitm_end_to_end
```
