# SCADAVT core — M0 / M1 (domain-neutral platform)

Implementation of the approved architecture (Arabic proposal V6+): a standalone,
domain-neutral SCADA/ICS platform. The physical process is an **independent
external application** connected over a public **Integration API**; the
water/EPANET simulator is only an example app, not part of the core.

This document is the M0 deliverable (the layer map, the decisions, the API
contract) and the M1 report (what was built, test results, limitations, next
steps). It is not another proposal.

## M0 — layer map (framework / external apps / configuration)

Every module is classified; the core knows nothing about any domain.

| Existing / new module | Layer | Notes |
|---|---|---|
| `core/integration.py` | **core** | public Integration API (extensible), client + server base |
| `core/legacy.py` | **core** | compatibility adapter for the thesis 17-byte PDU (reuses `protocols/wdsproto`) |
| `core/fieldbus.py` | **core** | neutral device image + **pluggable** field-bus; Modbus = default plugin (reuses `protocols/modbus`) |
| `core/control.py` | **core** | configurable control host (placement = `mtu` / `plc` / `distributed`); engine = `builder/rules.RuleEngine` |
| `core/timing.py` | **core** | event-stepped, non-blocking loop + end-to-end runner |
| `core/coregen.py` | **core** | minimal config-driven CORE session generator + gateway files |
| `core/labels.py` | **core** | two-dimensional ground-truth labels |
| `protocols/*`, `gateway/bridge.py`, `field/plc.py` | **core (reused)** | validated protocol/gateway/device code, reused |
| `examples/dummy_app.py` | **example app** | domain-neutral process (non-water) |
| `examples/water_app.py` | **example app** | EPANET water sim behind the API (wraps `wds/model.Plant`) |
| `wds/*`, `scenarios/town/` | **example (water)** | belongs to the water example, not the core |
| `master/control.py: Supervisor` | **example strategy** | water `pump_groups`+V1; to be loaded as a strategy, not core |
| `webui/`, Docker layer | **to remove/relocate** | superseded by the CORE-based design |

### Decisions
- **Integration API is the public contract; the thesis binary PDU is behind a
  compatibility adapter.** Public ids are arbitrary-length strings; data types
  are an open `{name, unit}` registry; the protocol is versioned and
  capability-negotiated. The 10-byte/1-byte thesis limits live only in
  `legacy.py`.
- **Field-bus is pluggable.** Modbus/TCP is the default, validated plugin;
  DNP3 / IEC-104 / EtherNet-IP can be added behind the same two interfaces. A
  device's protocol is a configuration choice.
- **Control placement is configuration**, not code: `mtu` (centralized),
  `plc` (local, no MTU), `distributed` (deferred). The MTU is optional.
- **Timing never blocks indefinitely**: every exchange is bounded; a missing /
  slow / failed value tags the sample (`OK`/`STALE`/`FAILED`) and the cycle
  proceeds. Reproducibility is **logical** (decoupled layers), **not** strict
  determinism at the CORE level — it is to be measured, not assumed.
- **Water is an external app**, swappable for any domain without touching core.

### Integration API contract (public)
Framing: 4-byte big-endian length + JSON. Connection: external app LISTENS,
SCADAVT gateway CONNECTS. Operations: `HELLO`, `LIST`, `ACQUIRE(id, role)`,
`CONTROL(id, value)`, `CLOCK`, `STEP(seconds)` (optional). Roles:
`measurement` (app→SCADAVT), `command` (SCADAVT→app), `state` (app→SCADAVT).
Quality on every reading: `OK` / `STALE` / `FAILED`. The channel is out of band
(not over the emulated SCADA network), preserving experiment validity.

## M1 — what was built and tested

`scadavt/core/` (+ `scadavt/examples/`), with `tests/test_core_m1.py`.

Three independent acceptance tests + end-to-end, all passing:

- **(a) Integration API alone** — round trip (measurement/command/state),
  extensible long identifiers and custom data types, non-blocking `FAILED` on a
  dead endpoint, and the legacy binary adapter still decodes the thesis PDU.
- **(b) Control engine alone** — identical commands under `mtu` and `plc`
  placement from the same engine/rules; placement validation; `needs_mtu`.
- **(c) CORE networking alone** — the Modbus field-bus stack round-trips a
  measurement and a command over a real socket (the stack that would traverse
  CORE); the session generator validates and emits a build script + gateway
  files for **two different configurations** (a centralized water-ish config and
  a local, MTU-less, two-RTU config), is **domain-neutral** (no water words in
  its output), and rejects bad configs.
- **End-to-end** — one control loop over the API in **both** placements
  (`LocalTransport` and `NetworkTransport` over loopback), driving the process
  from a low start; and the **water example app** (EPANET) over the same API
  filling a tank — reusing the validated `Plant`.
- **Reproducibility** — identical logical trajectory across repeated offline
  runs (determinism is explicitly *not* claimed for CORE timing).
- **Labels** — `attack_window` vs `malicious_confirmed` kept as distinct
  dimensions; benign traffic inside a window is counted as such.

### Test results (M1)
```
tests/test_core_m1.py ....... 15 passed
full suite .................. 63 passed, 1 skipped   (48 pre-existing + 15 new)
```

## M2 — generalized generator, device-node runner, disturbance measurement

New in `scadavt/core/`:

- **`device.py`** — the configuration-driven field-device node, i.e. *what a CORE
  node runs*: a field-bus server (Modbus plugin) on the SCADA network **and** an
  I/O-modules server on the back channel, built from a node's point list. Neutral;
  runnable as `python -m scadavt.core.device --session session.json --node PLC1`.
- **`coregen.py` (generalized)** — richer validation (IP and back-channel
  uniqueness, link endpoints, placement/MTU rules), switch/router nodes,
  per-node service commands pointing at the device runner, and
  `write_session()` emitting `session.json`, the two gateway INIs, a runnable
  `core_session.py` (CORE gRPC, compiles clean), and `run_devices.sh`.
- **`netmeasure.py`** — communication-layer disturbance measurement: RTT and
  failed/timed-out transaction rates for the Modbus stack, with delay+loss
  injected by a loopback `DegradedProxy` (stand-in for CORE `netem`). Measures
  the *communication* impact; asserts nothing about process deviation.

Tests `tests/test_core_m2.py` (4, all passing):

- device runner exercised on **both faces** over loopback (gateway writes a
  measurement via IOModules → master reads it via Modbus → master writes a
  command via Modbus → gateway reads it back via IOModules);
- generator validated on **three distinct configurations** (single+MTU, local
  MTU-less pair, switched 3-PLC+MTU), artefacts written and the gRPC script
  compiles; service commands cover every device; run script launches each;
- validation catches duplicate IPs, duplicate back channels and dangling links;
- disturbance shows up at the communication layer (baseline 0 failures → with
  delay+50% loss, failures and RTT rise), with no process-deviation assertion.

### Test results (M1+M2)
```
tests/test_core_m1.py + test_core_m2.py ... 19 passed
full suite ................................ 67 passed, 1 skipped
```

## M3 — standalone gateway, strategies, second plugin, distributed, bundle

New in `scadavt/core/` (+ `scadavt/examples/`):

- **`gateway.py`** — the standalone, domain-neutral gateway (deployment form of
  IOModuleGate): driven by `session.json` + the point mapping, it moves
  measurements/states from the external app (Integration API) into device back
  channels and reads commands back out to the app. Reuses the validated
  `gateway.bridge.DeviceLink` (IOModules client); every exchange is bounded.
- **`strategy.py`** + **`examples/water_strategy.py`** — control strategies are a
  config choice: `RuleStrategy` (generic rules) and the thesis water `Supervisor`
  (pump groups + V1) wrapped as an *example* strategy behind the same
  `decide(read, sim_h)` interface. `ControlHost.from_strategy(...)` hosts any.
- **`fieldbus.py` (second plugin)** — a `simpletcp` field-bus plugin beside
  Modbus, proving the layer is pluggable and that mixed-protocol topologies work
  (a reference second protocol, not a standard; real DNP3/IEC-104 drop in behind
  the same interfaces).
- **distributed placement** — demonstrated: a device runs local control that
  reads a neighbour's measurement over the SCADA net (peer field-bus client) and
  actuates its own output, with no MTU.
- **`bundle.py`** + **`capture.py`** — the synchronized experiment bundle
  (`trace.csv` + two-dimensional `labels.csv` + `metadata.json` + pcap refs on
  one reference clock) and a `tcpdump` capture wrapper (runs on a CORE/priv host).

Tests `tests/test_core_m3.py` (8, all passing): rule-strategy ≡ rule-engine; the
water Supervisor driving a pump as a strategy; the `simpletcp` plugin round trip;
mixed-protocol config + per-plugin default ports; the standalone gateway moving a
measurement app→device and a command device→app over the back channel; distributed
local control with a peer read; the experiment bundle with both label dimensions
and a pcap reference; the capture wrapper constructs.

### Test results (M1+M2+M3)
```
tests/test_core_m[123].py ... 27 passed
full suite .................. 75 passed, 1 skipped
```

## Limitations (honest)
- **CORE does not run in this sandbox** (no namespace privileges, no passwordless
  sudo, daemon not started). So the generated `core_session.py` is validated by
  compilation + structure, the device runner / field-bus / gateway are exercised
  over **loopback** (the same Modbus/IOModules stack CORE would carry), and the
  disturbance is injected by a loopback proxy instead of CORE `netem`. Running a
  generated session on live CORE and capturing synchronized pcaps (the
  `capture.py` wrapper) must be done on a CORE-capable, privileged host.
- `simpletcp` is a *reference* second field-bus plugin, not a standard protocol;
  real DNP3 / IEC-104 / EtherNet-IP remain to be implemented behind the same
  interfaces.
- The generator emits a runnable gRPC session script + gateway INIs; it does not
  emit a CORE `.imn` GUI session (optional, later).
- The distributed placement is demonstrated at loop level; a packaged
  per-node distributed control service is a later step.
- End-to-end on live CORE (generate → run nodes + gateway + external app →
  attack → capture → bundle, all on one host) is the remaining integration that
  this sandbox cannot execute.

## Next steps
- Run the whole pipeline on a CORE-capable host and record a real bundle
  (live pcaps + process trace + labels).
- Real industrial-protocol plugins (DNP3 / IEC-104) and a packaged distributed
  control service; optional CORE `.imn` emission.
