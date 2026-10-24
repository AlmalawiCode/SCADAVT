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

North star unchanged: **Python-first, GUI-optional, domain-independent.**

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

---

## 4. Schema changes (`config.schema.json`)

- **Add** `devices[].registers[]` (area enum, address, data_type, scale/offset,
  count, initial, access) as the native PLC data model.
- **Add** optional top-level `io_map[]` and `logic[]` (register-based control).
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
| `core/integration.py` | unchanged in spirit — it already *is* the external bridge; `io_map` drives it (its MEASUREMENT/COMMAND/STATE become the mapping `direction`). |
| `core/corelive.py` | `grid()`/`manual`/`_device` emit **register-based** PLCs (e.g. an `input_register` + a `coil`) and register-based `logic`; no mandatory `s`/`a`. (Keeps producing valid configs; the multi-PLC ring is expressed over registers.) |
| `cli.py` | `scaffold` emits register configs; `validate`/`run` unchanged. A `scadavt registers <cfg>` view is optional. |
| `apps/water/labui/config.py` + app | the water overlay emits `registers` + an `io_map` (tank level→input_register in; pump→coil out) instead of role-points. Sensor/actuator meaning lives here (**G2**). |
| `designer` (GUI) | PLC property panel edits a **register table** (add/remove rows: id, area, address, data_type, initial); control rules reference registers. An **optional** "External mapping" panel edits `io_map`. No forced `s`/`a`. |
| `service` (HTTP) | no shape change; it passes configs through to L1/L2. |

**Nothing in the CORE runtime changes semantically** — a PLC still runs a real
Modbus endpoint in its node; the MTU still talks real Modbus. We change *how the
register is declared* and *where sensor/actuator meaning lives*.

---

## 6. Back-compat & migration (preserve validated experiments)

The validated multi-PLC experiments (PR #19/#22; `tools/live_core_*`) use
`points` with `role` `measurement`/`command`. To keep them **running unchanged**:

- `points[{role,is_bool,address?}]` is accepted as a **legacy alias** that lowers
  to `registers` using the current `_table_for` rule (`measurement`/`state`→input
  register/discrete input; `command`→holding/coil). `mapping`+`rules` lower to
  `io_map`-less register `logic`. Marked deprecated; emits a one-line notice.
- New generators (`corelive`, designer, water overlay) move to **native
  registers**; a `scadavt migrate <old.json>` converter emits the register form.
- Net: every existing config and live test still passes; new configs are
  register-native. This is the safest path and is the recommended default.

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

---

## 8. Decisions already taken (engineering judgment — not asking)

- Legacy `points`/`rules`/`mapping` kept as a **deprecated compat alias** (lower
  to registers) rather than a hard break — protects validated work.
- `io_map` and `logic` are **new optional top-level sections**; `io_map` entries
  reference `(device, register)`.
- Register `area` enum = `coil|discrete_input|holding_register|input_register`;
  `data_type` = `bool|uint16|int16|uint32|int32|float32` (+`scale`/`offset`);
  access derived from area.
- Sensor/actuator wording is **removed from the generic layer** and used only in
  the external mapping (`direction: in|out`) and domain overlays.

## 9. Non-goals

Not adding more sensors/actuators to the GUI; not changing the CORE comms/attack
machinery; not changing the Python-first / GUI-optional / domain-independent
stance. Phase-2 PRs (#29 service, #30 designer) are unaffected structurally and
can proceed; the designer's PLC panel is updated under this correction once
approved.

---

**Proposed sequence after approval** (separate branches/PRs, no merge without
approval): (1) schema + `fieldbus.Register` + compat alias + validation; (2)
device/master/coregen on registers; (3) `corelive`/CLI generators → registers;
(4) `io_map` + water overlay as the external mapping; (5) designer register panel
(+ optional mapping panel); each with the A1–A6 tests.
