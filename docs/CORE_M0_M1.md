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

### Test results
```
tests/test_core_m1.py ....... 15 passed
full suite .................. 63 passed, 1 skipped   (48 pre-existing + 15 new)
```

## Limitations (honest)
- **CORE does not run in this sandbox** (no namespace privileges, no passwordless
  sudo, daemon not started). So the CORE *session generator* is validated
  structurally and the network behaviour is exercised over **loopback**, which
  carries the same Modbus stack. Running the generated session on live CORE,
  and measuring per-link `netem` delay/loss and pcap capture, must be done on a
  CORE-capable host (M1 test (c) network-disturbance measurement, M2).
- `distributed` (PLC↔PLC) placement is defined but not yet implemented.
- The generator emits a gRPC build script and gateway INIs; it does not yet
  emit a CORE `.imn` GUI session (M2, generalization).
- Only the Modbus field-bus plugin ships; others are interfaces only.
- `Supervisor` (water V1 strategy) is not yet reworked into a config-loaded
  strategy; the generic `RuleEngine` is the path used by the core.

## Next steps
- M2: generalize the CORE session generator; run on a CORE host; measure network
  disturbance at the communication layer (RTT, failed/dropped transactions) and
  capture synchronized pcaps.
- Rework `Supervisor` as an example strategy loaded from configuration.
- Add the synchronized experiment bundle export (pcap + process trace + metadata
  + two-dimensional labels) end to end.
