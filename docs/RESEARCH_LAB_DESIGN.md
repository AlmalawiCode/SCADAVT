# SCADAVT Research Lab — Design

*A portable, open-source visual laboratory for SCADA cybersecurity research,
built on the validated SCADAVT 2.0 testbed.*

Status: living design document. Phase P1 under implementation (2026-10).

---

## 1. Purpose and scope

SCADAVT 2.0 is a reproducible SCADA security testbed: a data-driven water
distribution plant (EPANET) coupled to an emulated SCADA network that carries
real Modbus/TCP traffic, deployed as containers by a generator. This document
specifies a **research laboratory** layer on top of it so that a researcher can,
through a web browser:

1. **Design and modify** SCADA networks with drag-and-drop components
   (PLCs, MTU, routers, sensors, actuators, pumps, tanks);
2. **Simulate** the industrial process and its network communication;
3. **Configure and execute** controlled cyberattacks — False Data Injection,
   replay, command manipulation, and Denial of Service — inside the isolated lab;
4. **Monitor** process behaviour and network traffic in real time;
5. **Automatically collect and label** normal and attack data for IDS research;
6. **Export reproducible datasets** suitable for scientific publication.

The laboratory's priorities, in order, are **scientific validity,
realistic cybersecurity experiments, reproducibility, and high-quality
datasets**. It is a research instrument, not a teaching demonstrator.

### Non-goals

- The GUI never replaces or alters the simulation physics or the SCADA
  protocol behaviour. It authors inputs and orchestrates runs.
- Attacks never leave the emulated lab network. No capability targets a real
  or external host.

---

## 2. Core principle — four planes, one untouched core

Scientific validity rests on a single rule:

> **The GUI never mutates running simulation state. It only produces the same
> declarative artifacts that the command line already consumes, and triggers the
> existing runner.**

Consequently every visual session is reproducible **headless**: the files the
GUI writes (`scada.yaml`, the EPANET `.inp`, and an `experiment.yaml`) are
exactly what `python -m scadavt.experiments.networked` reads. The GUI is a
convenience over the CLI, never a hidden dependency of a published result.

| Plane | Responsibility | Code |
|---|---|---|
| **Core** (unchanged) | EPANET plant, Modbus/TCP emulation, deployment generator — the scientific ground truth | `scadavt/wds/`, `scadavt/field/`, `scadavt/master/`, `scadavt/gateway/`, `scadavt/deploy/` |
| **Authoring** | visual editor ⇄ declarative scenario and experiment files, with validity checks | `scadavt/webui/` (backend) + web frontend; writes `scenario.py`-compatible YAML |
| **Attack** | declarative attack catalogue compiled to network actions or an isolated attacker container | `scadavt/attacks/` + attacker image in `deploy/compose.py` |
| **Observation + Dataset** | live monitoring, deterministic labelling, provenance manifest, dataset export | `scadavt/webui/` + `scadavt/analysis/` |

```
                 ┌──────────────── web browser (React Flow GUI) ────────────────┐
                 │  design canvas · attack panel · live monitor · dataset export │
                 └───────────────────────────┬──────────────────────────────────┘
                                              │ HTTP + WebSocket
                 ┌────────────────────────────▼─────────────────────────────────┐
   AUTHORING     │  FastAPI backend (scadavt.webui)  — wraps the existing API     │
   + OBSERVATION │  /scenario /validate /build /run /attack /stream /export       │
                 └───┬───────────────┬───────────────┬───────────────┬───────────┘
                     │ writes         │ calls          │ reads          │ derives
             scada.yaml / .inp   deploy.compose   container logs    labels + manifest
             experiment.yaml     networked.Runner  pcap / CSV        dataset bundle
                     │                │                │                │
                 ┌───▼────────────────▼────────────────▼────────────────▼───────┐
   CORE          │  SCADAVT 2.0 simulation + emulation (unchanged, validated)     │
                 └───────────────────────────────────────────────────────────────┘
```

---

## 3. Reuse map

Each research goal binds to existing, validated code; only the glue is new.

| Goal | Reuses (unchanged) | New code |
|---|---|---|
| 1. Drag-and-drop design | `scenario.py`, `deploy/compose.py` (`plan`, `write_configs`, `compose`) | editor → YAML writer, EPANET validity check |
| 2. Process + network simulation | `wds/server.py`, `field/plc.py`, `master/mtu.py`, `gateway/bridge.py` | none |
| 3. Attack execution | `experiments/networked.py` fault engine and `hook` kind | attack catalogue + attacker image |
| 4. Real-time monitoring | MTU HMI slave, `SIMCLOCK` back channel, pcap capture | WebSocket tailer |
| 5. Auto labelling | `faults.csv` (every window with wall + simulated time) | deterministic labeller |
| 6. Reproducible export | `summary.json`, seed, ground-truth + historian + connections + gateway CSV, per-router pcap | provenance manifest + join |

---

## 4. Attack subsystem

### 4.1 Why the networked mode

False Data Injection, replay, and command manipulation are **Modbus-payload
attacks**: they tamper with traffic in flight. The process-level mode
(`experiments/offline.py`) carries no traffic and models faults only by their
physical consequence, so these attacks are defined **only in the networked
(container) mode**, through an in-path attacker placed on the victim's field
LAN. This is the scientifically honest placement and it reuses the full Modbus
emulation path.

### 4.2 Attacks as data

An attack is a declarative record, not bespoke code. The catalogue
(`scadavt/attacks/`) compiles each record either to a **network action** (reuse
`tc`/`netem`/container pause, already in the runner) or to an **attacker
container** that the deployment generator attaches to the target field LAN.

```yaml
# experiment.yaml — attacks share the fault-window schema (start_h, end_h, target)
attacks:
  - {start_h: 40, end_h: 48, kind: fdi,     target: PLC6, tag: T2_LVL, mode: freeze}
  - {start_h: 60, end_h: 62, kind: replay,  target: PLC6, capture_h: [10, 12]}
  - {start_h: 80, end_h: 81, kind: command, target: PLC6, tag: V1_SET_O, value: 0}
  - {start_h: 90, end_h: 92, kind: dos,     target: plc6, rate: "50mbit"}
```

| Attack | Mechanism | Built on |
|---|---|---|
| **False Data Injection** | ARP MITM on the field LAN; rewrite sensor values in Modbus responses / IOModules replies (freeze, offset, or scripted series) | attacker container |
| **Replay** | capture a benign window, re-inject it during the attack window | attacker container |
| **Command manipulation** | rewrite MTU→PLC writes (coils / holding registers) in flight | attacker container |
| **Denial of Service** | SYN/Modbus flood, or `link_loss` / `router_stop` link control | partly existing (`tc`, pause) |

### 4.3 Labels are a byproduct

Every attack reports its exact active interval to `faults.csv` with both
wall-clock and simulated time, exactly as fault windows do today. Labelling is
therefore derived, never hand-annotated.

### 4.4 Isolation guarantee

Attacker containers live only in the generated lab network namespaces. The
catalogue refuses any attack whose `target` is not a real tag of a real device
in the generated topology (`catalog.resolve`), and each attacker is attached to
exactly one field network — never the back channel or the control LAN.

The attack runner additionally marks every lab network except the back channel
as Docker-`internal`, so no node (the attacker included) can egress to the
Internet through a router's host NAT. This is verified: from the attacker, the
victim PLC is reachable, lateral movement across the emulated SCADA network is
possible (the intended threat model — a foothold on one field LAN can route to
the MTU and other sites), but the plant's out-of-band ground-truth back channel
is unreachable and the Internet is unreachable. The back channel stays
host-reachable only so the runner can follow the simulated clock and collect the
ground truth. No attack primitive accepts an external address.

---

## 5. Reproducibility and datasets

A dataset release is the joined logs plus a **provenance manifest** the backend
emits automatically:

- `scenario_sha256`, EPANET `.inp` hash, the resolved `experiment.yaml`;
- `seed`, Docker **image digests**, the scadavt git commit;
- the full attack schedule with wall + simulated timestamps;
- derived labels — per row a **binary** label (normal / attack) and a
  **multiclass** label (attack kind).

The labeller joins `ground_truth.csv`, `historian.csv`, `connections.csv`,
`gateway.csv`, and the per-router pcap against the attack windows. Because every
input is hashed and the run is seeded, re-executing the manifest reproduces the
dataset deterministically — the standard a reviewer needs.

A dataset bundle is therefore:

```
dataset/
  manifest.json          # hashes, seed, image digests, git commit, attack schedule
  experiment.yaml        # the exact resolved experiment
  process.csv            # ground truth + historian, one row per control cycle
  network.csv            # connection/gateway features (and pcap-derived flows)
  labels.csv             # binary + multiclass, aligned to process.csv / network.csv
  pcap/                  # raw captures (optional, large)
  README.md              # how to reproduce
```

---

## 6. Portability and distribution

The laboratory is a self-contained, GitHub-ready artifact.

- **Single-command launch.** `docker compose up` (wrapped by a `scadavt-lab`
  helper / `make lab`) builds the images, starts the backend and the web GUI,
  and serves the browser interface on a local port. No manual configuration.
- **Everything bundled.** Images carry all Python and system dependencies; the
  `town` example scenario and the `e1…e4` example experiments ship in the repo;
  documentation (this file, `NETWORKED_INTERFACES.md`, a quick-start) ships with
  it.
- **Browser GUI.** The frontend is static assets served by the backend; no
  local toolchain required to *use* the lab (a toolchain is needed only to
  *rebuild* the frontend from source, and the built assets are committed).
- **Modular and extensible.** New components, attack kinds, and dataset
  exporters are added by dropping a module into `scadavt/attacks/` or a
  component/exporter registry — no change to the core.

### Repository layout (added by this work)

```
scadavt/
  webui/              # FastAPI backend + built frontend assets
    api.py            # routes
    live.py           # WebSocket live-data bridge
    dataset.py        # labeller + manifest + export
    static/           # built React Flow GUI (committed)
    frontend/         # frontend source (React Flow) — built into static/
  attacks/            # attack catalogue (compiles to actions / attacker image)
docs/
  RESEARCH_LAB_DESIGN.md   # this document
  QUICKSTART.md            # install + launch + first dataset
deploy/
  docker-compose.lab.yml   # single-command lab stack
```

---

## 7. Phased delivery

Each phase is validated (tests + an example run) before the next begins.

| Phase | Deliverable | Validation | Status |
|---|---|---|---|
| **P1** | FastAPI backend + web viewer of the `town` scenario; live tags; run / stop via the existing `Runner`; single-command Docker launch | backend unit tests; GUI loads topology; a short run streams live data | ✅ done |
| **P2** | `scadavt/attacks/` catalogue + isolated attacker container; **FDI** end-to-end | FDI measurably perturbs the victim tag; window logged to `faults.csv`; isolation verified | ✅ done |
| **P3** | Replay, command-manipulation, DoS; labeller + provenance manifest; dataset export | labelled dataset reproduces deterministically from its manifest; label/window alignment test | ✅ done |
| **P4** | Drag-and-drop **authoring** with live EPANET validity check; attack panel in the GUI | edited scenario round-trips to valid `scada.yaml` + `.inp`; hydraulic solve succeeds | next |

**P2 result.** A False Data Injection against `PLC1.T2_LVL` (`mode=const`,
6.5 m, window 8–16 h) was run on the full 51-container town: inside the window
the MTU historian records a constant 6.5 m while the true tank level diverges
(the supervisor throttles inflow believing the tank is full); outside it the
MTU-seen value matches ground truth. The window is logged to `faults.csv` with
wall and simulated time. Isolation was probed at runtime (Internet and back
channel unreachable from the attacker; victim and SCADA lateral movement
reachable). Delivered modules: `scadavt/attacks/{catalog,proxy,handlers,arp,
mitm,deploy,runner}.py`, example `experiments/a1_fdi_t2.yaml`,
`tests/test_attacks.py` (unit + Docker-gated end-to-end).

**P3 result.** Three further attack kinds and the dataset pipeline were added
and validated on the town:

- **Command manipulation** (`command`) — the attacker rewrites the MTU's V1
  flow-setpoint writes to 0; the valve flow collapsed (in-window mean 387 vs
  1411 L/min) while the MTU kept issuing nonzero commands. Same MITM proxy,
  rewriting requests instead of responses (`CommandHandler`).
- **Replay** (`replay`) — records legitimate sensor readings and replays them
  cyclically (`ReplayHandler`, same validated read-rewrite path as FDI).
- **Denial of service** (`dos`) — deterministic 100 % packet loss on the
  victim's field interface (reuses the base runner's proven `tc netem`
  mechanism); the MTU's polls of the victim failed (in-window success rate 0.00,
  other devices unaffected). An optional traffic-flood agent (`attacks/dos.py`)
  is also provided.
- **Dataset pipeline** (`attacks/dataset.py`) — joins ground truth, MTU
  historian and network logs, derives per-row **binary** and **multiclass**
  labels from the logged attack windows, and writes a bundle
  (`process.csv`, `network.csv`, `labels.csv`, `manifest.json`, `README.md`).
  The manifest pins seed, scenario and EPANET SHA-256, Docker image digest and
  git commit, so the dataset reproduces deterministically. Verified: a
  command+DoS run produced correctly aligned labels (normal / command / dos).

Topology *authoring* (P4) is deliberately last: keeping edits hydraulically
valid is the one genuinely hard part, whereas P1–P3 already deliver the full
research workflow — visual monitoring, controlled attacks, and reproducible
labelled datasets.

---

## 8. Scientific-integrity checklist

Every phase must preserve these invariants:

1. The simulation core is unmodified; the GUI touches only declarative inputs.
2. A GUI-authored run and its headless CLI equivalent produce identical output
   for the same seed.
3. Attacks are confined to the lab network; no primitive accepts an external
   target.
4. Each dataset ships a manifest sufficient to reproduce it bit-for-bit.
5. Labels derive from the logged attack schedule, never from manual annotation.
