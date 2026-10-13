# SCADAVT guide

## 1. The five guidelines

| | Guideline | In SCADAVT |
|---|---|---|
| G1 | Select a communication infrastructure that talks to the outside world and accepts new protocols and services. | CORE network emulator (original); containers (planned port). |
| G2 | Develop the SCADA components as services: MTU, HMI, historian (high level); PLCs/RTUs/IEDs (field level). | Modbus/TCP slave + I/O modules simulator per PLC; MTU with HMI server. |
| G3 | Implement at least one standard SCADA protocol inside the emulation. | Modbus/TCP (`scadavt/protocols/modbus.py`). |
| G4 | Link the internal world (emulated network) and the external world (plant, HMI client) through a gateway. | IOModuleGate + meta-converter over a back channel. |
| G5 | Simulate a realistic controlled environment with a validated model and measured inputs. | EPANET 2.3 + demand from Melbourne Water weekly use per person. |

## 2. Protocols

### IOModules (gateway ↔ I/O modules simulator), 19 bytes, big-endian

| Field | Bytes | Meaning |
|---|---|---|
| TransactionNo | 4 | Read and write counters start at 1; a response echoes the number; **0 = output not ready** |
| Function code | 1 | 1 read, 2 write, 0 terminate |
| ProcessID | 10 | ASCII tag name, NUL-padded |
| Data | 4 | float32 (0 in a read request) |

### WaterSystem / WDS (meta-converter ↔ plant server), 17 bytes, big-endian

| Field | Bytes | Meaning |
|---|---|---|
| ProcessID | 10 | EPANET element ID (`SIMCLOCK` returns simulated hours) |
| Function code | 1 | 1 acquisition, 2 control (0xFF in an error response) |
| Parameter type | 1 | 0 node, 1 link |
| Data type | 1 | 1 level, 2 pressure, 3 demand, 4 head, 5 volume, 10 flow, 11 status, 12 setting, 13 speed, 14 energy, 15 deficit |
| Data | 4 | float32 |

## 3. Scenario format (`scada.yaml`)

```yaml
epanet_file: town.inp
population: {A1: 4500, ...}          # people per demand node
tank_max_level: {T1: 7.0, ...}
fields: {A: [PLC2], B: [PLC3, PLC4], ...}
plcs:
  PLC1:
    field: D
    tags:
      - [T2_LVL, T2, node, level, i]       # sensor  -> input register (float32)
      - [V35A_ST_O, V35a, link, status, o] # actuator -> coil
control: {...}                           # pump groups, V1 balancing parameters
```

Register assignment: `status` + `i` → discrete input (D), `status` + `o` → coil (C),
other `i` → input registers (A, two per float), other `o` → holding registers (H).

## 4. Supervisory logic

Pump groups: on below `on_below` × max level, off above `off_above` × max level.

Valve V1 between T2 and T3 (flow balancing):

    q* = (A3 (f − b) + A2 c) / (A2 + A3)
    q  = clip(q* + K (h2 − h3), 0, q_max)

f inflow to T2, b demand of area B, c demand of area C, A2/A3 tank cross-sections,
h2/h3 levels. Missing readings keep the previous decision.

## 5. Experiments (paper Section 6)

| ID | What | Runs |
|---|---|---|
| E1 | V1 fixed vs supervised, summer week (23 Jan 2025) | 2 × 10 seeds |
| E2 | Same, one week per season of 2025 | 4 × 2 × 5 seeds |
| E3 | Fault windows (15–20 h, 55–57 h, 100–105 h): PLC6 channel lost / P4,P5 held off | 2 × 10 seeds |
| E4 | One fault window at hour 30, length 1–12 h | 8 × 2 × 5 seeds |

Measures: hours below the critical level (15 % of max), events below critical,
unmet demand (m³, pressure-driven analysis), pump energy (kWh).
