# Live attack #3 — sensor-data deception (FDI & replay), in-transit on CORE

**Classification.** In-transit **sensor-data attacks** on the *read* path: the
attacker rewrites the PLC→MTU read response so the controller is fed **false
measurements** and makes a harmful control decision **on its own**. The actuator
command is **not** tampered — the deception is entirely on the sensor path. This
is distinct from the command attacks (#1 injection, #2 in-transit write rewrite).

- **FDI** (false data injection): force the tank-level read to a constant "safe"
  high value. MITRE ATT&CK for ICS: **T0856** (Spoof Reporting Message),
  **T0832** (Manipulation of View).
- **Replay**: record genuine readings and replay them so the MTU sees a plausible
  past signal while the plant diverges. MITRE: **T0856** / replay.

Reuses the **stable L3-routed MITM** (attacker = router between the MTU and PLC
subnets; no ARP; see [`ATTACK_mitm_in_transit.md`](ATTACK_mitm_in_transit.md))
and the existing `attacks/handlers.py:FDIHandler` / `ReplayHandler`. **EPANET,
the gateway, the scenario and the MTU's control logic are unchanged.**
Tool: `tools/live_core_sensor_attack.py --kind {fdi,replay}`. Ground truth (true
tank level) is read from the EPANET app over the Integration API (off the attacked
path); the MTU-perceived level is read over Modbus (through the MITM) — their
divergence is the attack, the real tank trajectory is the impact.

## FDI — VERIFIED live on CORE (6/6)
- **Connection stable** (deception, not disruption): 0 `socket.send` exceptions.
- **Benign:** perceived ≈ true (≤ ~0.8 m, read-timing skew on a moving tank),
  pump held at 0.7.
- **Attack:** the MTU **perceives a constant 6.0 ("full")** while the **true
  level diverges by 6.0 m and empties to ~0**; the controller, believing the tank
  is full (6.0 > the 5.5 off-threshold), **commands the pump OFF** (100 % of the
  window) — its own decision on false data.
- **Physical impact:** the **real tank drains to ~0** (benign min ~3.1 m) while
  the operator's view shows a healthy "full" tank — a manipulation-of-view with a
  real cyber-physical consequence.

## Replay — VERIFIED live on CORE (4/4)
- Connection stable (0 exceptions); benign perceived ≈ true (~0.06 m).
- **Attack:** the perceived level is a **replayed earlier signal** that **diverges
  from the true level (~1.6 m)** during the window — the operator/controller is
  shown a plausible but stale trace while the plant drifts.

## Evidence (per run, `evidence/`)
`benign_trace.csv` / `attack_trace.csv` with columns `true_level`,
`perceived_level`, `pump_cmd`; the attacker agent log (`attacker_agent.csv`,
window + hits); the MTU commanded log. The `FDIHandler`, `ReplayHandler` and the
proxy are additionally covered by loopback unit tests in `tests/test_attacks.py`.

Reproduce:
`SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python /opt/core/venv/bin/python tools/live_core_sensor_attack.py --kind fdi`
(and `--kind replay`). Needs a running `core-daemon`.

## Limitations
- FDI with a constant high value is the dramatic case (controller stops pumping).
  Replay's hydraulic impact is milder and depends on how far the recorded trace
  drifts from the live plant; here it is demonstrated as a clear **view**
  divergence (perceived vs true), with a smaller physical effect in this short
  window.
- `true` and `perceived` are read at slightly different instants, so a sub-metre
  benign gap on a moving tank is read-timing skew, not falsification.
- Single PLC; not scaled to the multi-PLC network.
