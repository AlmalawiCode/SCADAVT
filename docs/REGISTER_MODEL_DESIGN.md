# Architectural Correction — Register-based PLC model (for approval)

**Status: DESIGN ONLY. No implementation or merge until approved.**
Scope: make the generic PLC a **Modbus register device**, and move sensor/actuator
meaning into an **optional external mapping layer** — without breaking the
validated multi-PLC communication/control experiments.

---

## 1. The requirement (restated)

- **G1** A PLC is an independent controller with a **Modbus data model** — coils,
  discrete inputs, holding registers, input registers — **not** an object that
  contains sensors/actuators. Register meaning is configurable, independent of
  any physical process; analog values are registers + encoding/scaling/type.
- **G2** Sensors and actuators belong to the **external physical world**
  (EPANET, hardware, domain models), not the generic framework.
- **G3** An **optional external mapping layer** connects external variables / I/O
  to PLC Modbus addresses (identity+address, area+address, data type/encoding/
  scaling, direction/access, sync behaviour, write-ownership). Mappings are
  optional — a PLC must work with none.
- **G4** The generic designer lets users create nodes/links and **configure each
  PLC's Modbus register areas** (addresses, initial values, access) and control
  logic over those registers; domain components are optional overlays. Creating a
  PLC must **not** require creating sensors/actuators.
- **G5** Preserve real PLC/Modbus behaviour: each PLC runs its own Modbus endpoint
  in its CORE node; masters talk to it over real Modbus at the configured
  addresses; the framework must not fabricate physical behaviour just because a
  register exists.
- **G6** A modular **Adapter/Driver architecture** supplies register values from
  three interchangeable sources behind one contract — **simulated** devices,
  **real hardware** (gated, safety-limited), and **industrial process
  simulators** — selected by configuration alone, never by changing the generic
  PLC (§3.4).

North star unchanged: **Python-first, GUI-optional, domain-independent**, and one
shared register-based architecture across pure PLC networks, software simulation,
hardware-in-the-loop, and industrial co-simulation.

---

## 2. Where the current code bakes in sensor/actuator (evidence)

The Modbus machinery already exists and is correct — but it is **gated behind a
sensor/actuator `role`** instead of being the first-class model.

| Location | What it does today | Problem |
|---|---|---|
| `scadavt/core/fieldbus.py` `_table_for(role,is_bool)` (L41–45) | **Derives** the Modbus table from the role: `measurement`/`state`→input reg / discrete input; `command`→holding / coil | The register *area* is chosen by sensor/actuator semantics, not declared (**G1**) |
| `fieldbus.DevicePoint` (L22–29) | point = `(role, is_bool, address)` | No first-class area/data-type/scale; role is mandatory |
| `scadavt/core/schema/config.schema.json` (L50–53, 109–112) | device `points[].role` **enum `["measurement","command"]`** required; `mapping[].role` likewise | Generic schema **requires** sensor/actuator roles (**G1/G4**) |
| `scadavt/core/device.py` `device_from_node` (L34–45) | builds `DevicePoint`s from `points[{role,is_bool}]` | PLC defined in sensor/actuator terms |
| `scadavt/core/master.py` (L58–85) | binds `mapping` by role: `command`→write target, else→measurement read | Control engine framed in measurement/command, not read/write registers |
| `scadavt/core/corelive.py` `grid()`/`manual`/`_device` (L116–119, 169) | every PLC auto-gets `s` (measurement) + `a` (command) | Generators force sensors/actuators onto every PLC (**G4**) |
| `scadavt/designer/static/designer.js` `addNode('plc')` (L57–59) | every PLC auto-gets `s`+`a` points and `S/A` mapping | GUI forces sensors/actuators (**G4**) |
| `scadavt/core/integration.py` `MEASUREMENT/COMMAND/STATE` (L40–42) | roles for the **external app** bridge | Correct *here* — this is the external side; it should be the home of sensor/actuator meaning (**G2/G3**) |
| `scadavt/apps/water/labui/config.py` (L43–50) | maps water elements → point roles (tank→LEV measurement, pump→CMD command) | Already a domain overlay, but it emits generic role-points instead of registers + a mapping (**G2/G3**) |

**Good news:** `fieldbus.Device.register_map()` already lays points into the four
Modbus tables with addresses/refs, and addresses can be *pinned* in config. The
correction mostly **inverts the dependency** (declare the register area directly;
stop deriving it from a role) and **relocates** sensor/actuator meaning to a
mapping layer — it does not rebuild the Modbus stack.

---

## 3. Target model

### 3.1 Generic PLC = Modbus registers (G1)

```jsonc
{
  "id": "PLC1", "ip": "10.0.1.1", "protocol": "modbus", "port": 502,
  "registers": [
    // area picks the Modbus table directly; access follows from the area
    {"id": "lvl",  "area": "input_register", "address": 0, "data_type": "float32",
     "scale": 1.0, "offset": 0.0, "initial": 0.0},
    {"id": "pump", "area": "coil",           "address": 0, "data_type": "bool",
     "initial": false}
  ]
}
```

- `area` ∈ `coil | discrete_input | holding_register | input_register` — **the
  table is declared, not inferred.** `access` is implied (`input_register`,
  `discrete_input` = read-only to a master; `holding_register`, `coil` =
  read/write) and may be stated explicitly.
- `data_type` ∈ `bool | uint16 | int16 | uint32 | int32 | float32` (extensible),
  with optional `scale`/`offset` for engineering values, `count` for arrays,
  `initial` for power-on value. No `role`, no `measurement`/`command`.
- Addresses are explicit (or auto-packed per area, as today for unpinned points).

### 3.2 Control logic over registers (G1/G5)

Rules and the MTU bind to **register data points**, not roles:

```jsonc
"control": { "placement": "mtu" },
"logic": [
  {"id": "r1",
   "if":   [{"device": "PLC1", "register": "lvl", "op": "below", "value": 3.0}],
   "then": [{"device": "PLC2", "register": "pump", "set": 1}]}
]
```

The MTU **reads** any readable register and **writes** any writable register over
real Modbus at its address — "readable/writable" is the Modbus area, not a
domain role (**G5**). (Legacy `rules` with `comp`/element are still accepted — §6.)

### 3.3 Optional external mapping layer (G2/G3)

A new **optional** top-level section ties external process variables to PLC
registers. It is the *only* place sensor/actuator meaning lives:

```jsonc
"io_map": [
  {"external": {"source": "epanet", "element": "T1", "var": "level"},
   "device": "PLC1", "register": "lvl",
   "direction": "in",                      // in  = external -> register (a sensor)
   "scale": 1.0, "offset": 0.0, "update": "per_cycle", "owner": "process"},
  {"external": {"source": "epanet", "element": "P1", "var": "status"},
   "device": "PLC1", "register": "pump",
   "direction": "out",                     // out = register -> external (an actuator)
   "owner": "plc"}
]
```

- `direction: in` ⇒ the external process *writes* the register (sensor feed);
  `direction: out` ⇒ the register *drives* the external process (actuator).
- `owner` resolves conflicting writes (`process` vs `plc` vs `mtu`).
- **Absent `io_map` ⇒ the PLC runs as a pure Modbus device** (**G3**). The water
  app supplies its `io_map`; the generic core never mentions water.

### 3.4 Adapter / Driver architecture (the external integration layer)

The `external` side of every `io_map` entry is produced/consumed by a pluggable
**Adapter** (driver). Adapters are the *only* code that knows about a physical
process; the generic PLC never changes. Three interchangeable categories share
**one contract**, selected purely in config:

| Category | Adapter | Use | Default in tests |
|---|---|---|---|
| **Simulated** | `simulated` | software sensors/actuators (constant, function, script, simple dynamics) for experiments + CI | ✔ real |
| **Real hardware** | `hardware` | physical I/O via a device driver/gateway (serial/USB/GPIO/field-Modbus) | ✖ **mock only** |
| **Process simulator** | `simulator` | industrial co-sim (EPANET, energy, …) via the Integration API | ✔ real |

```jsonc
"adapters": [
  {"id": "sim1",    "kind": "simulated", "spec": {"S1": {"wave": "ramp", "min": 0, "max": 6}}},
  {"id": "epanet1", "kind": "simulator", "engine": "epanet", "model": "net.inp"},
  {"id": "hw1",     "kind": "hardware",  "driver": "modbus_serial",
                    "authorized": false, "params": {"port": "/dev/ttyUSB0"}}
]
```

An `io_map` entry names the adapter plus its external variable; the
register↔external binding is **adapter-agnostic**, so swapping the adapter
changes only `adapters`/`io_map`, never the PLC (**A7**):

```jsonc
"io_map": [
  {"adapter": "epanet1", "external": {"element": "T1", "var": "level"},
   "device": "PLC1", "register": "lvl", "direction": "in",
   "data_type": "float32", "scale": 1.0, "offset": 0.0,
   "update": "per_cycle", "quality": "hold_last", "owner": "process"},
  {"adapter": "hw1", "external": {"channel": "DO0"},
   "device": "PLC1", "register": "pump", "direction": "out",
   "limits": {"min": 0, "max": 1}, "owner": "plc"}
]
```

**Adapter contract** (`scadavt.core.adapters.Adapter`, an ABC):

- **Lifecycle** — `configure(cfg)` → `open()` (connect / load model) → `start()`
  (begin sampling) → per-cycle `read(var)` / `write(var, value)` → `stop()` →
  `close()` (release). It is a context manager; `close()` is idempotent and runs
  on any exception; on shutdown an output adapter drives outputs to a declared
  **safe state**. A failing adapter never crashes the PLC (which runs
  independently) — see error handling.
- **Data direction** — each mapped variable is `in` (adapter → register, a
  producer/sensor) or `out` (register → adapter, a consumer/actuator). An adapter
  declares the directions it supports; a mismatch is a validation error.
- **Register permissions / Modbus semantics (G5)** — an `in` mapping makes the
  adapter the **internal data producer** of the register: it writes the producer
  side of a protocol **read-only** area (`input_register` / `discrete_input`), so
  a master can *read* it but still **cannot write** it over Modbus — the standard
  read-only distinction is preserved (**A8**). An `out` mapping reads a
  **read/write** register (`holding_register` / `coil`) that the PLC/MTU wrote and
  applies it externally. Writing an `in` register from an adapter, or mapping
  `out` onto a read-only area, is rejected.
- **Encoding / scaling** — the mapping applies `data_type` (+ endianness) and
  `scale`/`offset` to pack engineering values into / out of the register; `out`
  values are range-checked.
- **Sampling / synchronization** — `update` ∈ `per_cycle | rate_hz:<n> |
  on_change`; `sync` ∈ `lockstep` (advance the simulator in step with the control
  cycle) or `async` (free-running, latest value). `owner` ∈ `process | plc | mtu`
  resolves conflicting writers to one register deterministically.
- **Error handling** — every value carries a **quality** (`OK | STALE | FAILED`);
  on adapter read/write failure the policy `quality` ∈ `hold_last | default |
  fault` applies, with bounded retry/backoff. Faults are logged and surfaced, not
  swallowed, and never propagate into a PLC crash.
- **Resource cleanup** — sockets/handles/model instances released on `stop`/
  error; hardware outputs returned to safe state; no leaked CORE sessions or
  background tasks.

**Real-hardware safety (category 2, mandatory):** a `hardware` adapter does
nothing unless **both** `authorized: true` (explicit, per adapter) **and** an
out-of-band launch gate `SCADAVT_ALLOW_HARDWARE=1` are set; otherwise it refuses
to open. Every `out` variable **must** declare `limits: {min,max}` and writes are
clamped (out-of-range rejected + logged). **Automated tests never actuate real
hardware** — they use a `mock` hardware driver (no real I/O), and the real driver
refuses to run without the gate (**A9**).

---

## 4. Schema changes (`config.schema.json`)

- **Add** `devices[].registers[]` (area enum, address, data_type, scale/offset,
  count, initial, access) as the native PLC data model.
- **Add** optional top-level `adapters[]` (id, kind ∈ `simulated|hardware|
  simulator`, driver/engine, authorized, params/spec) and `io_map[]` (adapter,
  external, device, register, direction, data_type, scale/offset, update, sync,
  quality, limits, owner) and `logic[]` (register-based control).
- **Keep** `devices[].points[]` and `mapping[]`/`rules[]` as **deprecated,
  still-accepted** legacy forms (role enum retained) — see §6.
- Validation (`coregen.validate`, authoritative per R7) gains register checks:
  area enum, address ≥ 0, unique (area,address) per device, data_type known,
  `io_map`/`logic` references resolve to real devices+registers.

---

## 5. Impact on existing code (by module)

| Module | Change |
|---|---|
| `core/fieldbus.py` | `DevicePoint` → `Register(id, area, address, data_type, scale, offset, count, initial)`. Delete `_table_for` (area is explicit). `set_input/get_output/set_output` → generic `read(area,addr)/write(area,addr)`. `register_map()` stays (now trivially the declared areas). |
| `core/device.py` | `device_from_node` builds from `registers`; a **compat shim** lowers legacy `points[{role,is_bool}]` → registers. `--seed` seeds a register by id. |
| `core/master.py`, `core/timing.py` | bind `logic`/`io_map` by `(device, register)`; "read" = read a readable register, "write" = write a writable one. Legacy role-mapping lowered to the same. |
| `core/coregen.py` | `register_maps`/`address_plan`/`validate` consume registers; legacy points lowered first. Session/service-command generation unchanged. |
| `core/adapters/` **(new)** | the pluggable driver layer: `Adapter` ABC + lifecycle, a `registry`, and built-ins `simulated`, `hardware` (with a `mock` driver + the real-HW safety gate), `simulator` (base). The run loop drives adapters per cycle and applies encoding/scaling/quality/owner. |
| `core/integration.py` | becomes the transport used by the `simulator` adapter; its MEASUREMENT/COMMAND/STATE collapse into the mapping `direction`. No new domain knowledge. |
| `core/corelive.py` | `grid()`/`manual`/`_device` emit **register-based** PLCs (e.g. an `input_register` + a `coil`) and register-based `logic`; no mandatory `s`/`a`. (Keeps producing valid configs; the multi-PLC ring is expressed over registers.) |
| `cli.py` | `scaffold` emits register configs; `validate`/`run` unchanged. A `scadavt registers <cfg>` view is optional. |
| `apps/water/labui/config.py` + app | the water overlay becomes a **`simulator` adapter** (EPANET) plus `registers` + `io_map` (tank level→input_register `in`; pump→coil `out`). Sensor/actuator meaning lives here (**G2**); the core never imports it. |
| `designer` (GUI) | PLC property panel edits a **register table** (add/remove rows: id, area, address, data_type, initial); control rules reference registers. An **optional** "External mapping" panel edits `io_map`. No forced `s`/`a`. |
| `service` (HTTP) | no shape change; it passes configs through to L1/L2. |

**Nothing in the CORE runtime changes semantically** — a PLC still runs a real
Modbus endpoint in its node; the MTU still talks real Modbus. We change *how the
register is declared* and *where sensor/actuator meaning lives*.

---

## 6. Back-compat & migration (preserve validated experiments)

The validated multi-PLC experiments (PR #19/#22; `tools/live_core_*`) use
`points` with `role` `measurement`/`command`. To keep them **running unchanged**:

- `points[{role,is_bool,address?}]` is accepted as a **one-way, transitional
  alias** that lowers to `registers` using the current `_table_for` rule
  (`measurement`/`state`→input register/discrete input; `command`→holding/coil).
  `mapping`+`rules` lower to register `logic`.
- **No two competing models.** The alias is a thin lowering, never a second
  authoring model: a device that declares **both** native `registers` and legacy
  `points` is a **validation error**; the register model is the single source of
  truth. The alias emits a deprecation warning and is **removed on a scheduled
  milestone** (tracked here as *M-legacy-drop*) once generators + saved scenarios
  are migrated.
- A `scadavt migrate <old.json>` converter rewrites any legacy config to the
  register form; new generators (`corelive`, designer, water overlay) emit native
  registers from day one.
- Net: every existing config and live test still passes during the transition,
  and the project converges on **one** register-based model.

---

## 7. Acceptance criteria (for your review)

- **A1 — pure register PLC.** A PLC defined with **only** `registers` (no role, no
  io_map) builds and runs; the MTU reads/writes its registers over real Modbus.
- **A2 — register-level comms.** PLC↔PLC and MTU↔PLC exchange happens through
  actual Modbus reads/writes at configured addresses (pcap + value read-back),
  with no sensor/actuator concept in the generic config.
- **A3 — optional external mapping.** An external process (EPANET) maps a variable
  to a PLC input register via `io_map` (`direction: in`) and drives an actuator
  from a coil (`direction: out`); **removing the io_map leaves the PLC working**.
- **A4 — reuse across domains.** The *same* PLC definition runs under two
  different process models (e.g. water vs a synthetic process) by swapping only
  the `io_map`/overlay — the PLC's registers/implementation are untouched.
- **A5 — one model, API≡GUI.** A register-based config authored in Python opens in
  the designer and vice-versa (lossless); both use the same schema + validator.
- **A6 — no regression.** Legacy role-based configs (the multi-PLC experiments)
  still validate and run identically via the compat alias; full unit suite green;
  live regressions (closed-loop, MITM, FDI/replay, multi-PLC) unchanged.
- **A7 — adapter interchangeability.** The **same** PLC config runs unchanged while
  only `adapters`/`io_map` changes across (a) a `simulated` sensor, (b) a `mock`
  `hardware` driver, and (c) a `simulator` (EPANET) — each produces the register
  value the MTU reads over real Modbus.
- **A8 — Modbus read-only integrity.** An adapter `in`-mapped to an
  `input_register` sets it internally; a master **read** returns the value, but a
  master **write** to that input register is refused (protocol-correct) — proving
  the read-only-vs-internal-producer distinction (**G5**).
- **A9 — hardware safety.** The real `hardware` adapter refuses to open without
  `authorized: true` **and** `SCADAVT_ALLOW_HARDWARE=1`; tests use the `mock`
  driver and never touch real I/O; `out` writes are clamped to `limits`, and
  out-of-range writes are rejected.
- **A10 — lifecycle & cleanup.** Adapters open/close deterministically (context
  manager, idempotent close); a failing adapter degrades via `quality`
  (`hold_last|default|fault`) **without crashing the PLC**; resources are released
  on stop/exception and outputs return to a safe state.
- **A11 — single model.** A device mixing native `registers` and legacy
  role-`points` is **rejected**; the alias emits a deprecation notice; `scadavt
  migrate` yields a register-only config.

---

## 8. Decisions already taken (engineering judgment — not asking)

- Legacy `points`/`rules`/`mapping` is a **transitional one-way alias**, not a
  second model: mixing it with native `registers` on a device is rejected, it
  warns, and it is **removed on a scheduled milestone** (M-legacy-drop).
- `adapters`, `io_map` and `logic` are **new optional top-level sections**;
  `io_map` entries reference `(adapter, external)` ↔ `(device, register)`.
- Register `area` enum = `coil|discrete_input|holding_register|input_register`;
  `data_type` = `bool|uint16|int16|uint32|int32|float32` (+`scale`/`offset`);
  access derived from area.
- Three adapter kinds (`simulated|hardware|simulator`) behind one `Adapter`
  contract; `hardware` is gated by `authorized:true` + `SCADAVT_ALLOW_HARDWARE=1`
  and never actuated in tests (mock driver).
- Sensor/actuator wording is **removed from the generic layer** and used only in
  the adapters / external mapping (`direction: in|out`) and domain overlays.

## 9. Non-goals

Not adding more sensors/actuators to the GUI; not changing the CORE comms/attack
machinery; not changing the Python-first / GUI-optional / domain-independent
stance. Phase-2 PRs (#29 service, #30 designer) are unaffected structurally and
can proceed; the designer's PLC panel is updated under this correction once
approved.

---

**Proposed sequence after approval** (separate branches/PRs, no merge without
approval; validate with real CORE + Modbus where applicable):

1. **Register core** — schema (`registers`) + `fieldbus.Register` + transitional
   alias + mixed-model rejection + validation. Tests A1, A8, A11.
2. **Register runtime** — device/master/coregen on registers; live PLC↔PLC /
   MTU↔PLC over Modbus. Tests A2, A6 (legacy regressions).
3. **Generators** — `corelive`/CLI emit register-native configs; `scadavt
   migrate`. Test A5 foundation.
4. **Adapter layer** — `core/adapters/` (`Adapter` ABC + lifecycle, registry,
   `simulated`, `hardware`+`mock`+safety gate, `simulator`) + `adapters`/`io_map`
   schema + the run loop. Tests A3, A7, A9, A10.
5. **Water overlay** — EPANET as a `simulator` adapter + its `io_map`. Test A4
   (same PLC under water vs a synthetic simulated process).
6. **Designer** — register table panel + optional external-mapping/adapter panel.
   Test A5 (API≡GUI), on the Phase-2 designer branch.

Each milestone keeps the full unit suite green and re-runs the live regressions.
