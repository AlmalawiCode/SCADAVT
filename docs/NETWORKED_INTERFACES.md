# Networked mode: what is ready and what you must write

The networked testbed runs every node of the case study as a container on an
emulated, routed network. Everything **around** the SCADA programs is ready and
tested. Three programs remain to be written. This document is their contract:
if a program follows it, it plugs into the deployment, the experiment runner and
the analysis without further changes.

## 1. What is ready

| Part | Where | Tested how |
|---|---|---|
| WaterSystem server (plant over TCP, WDS protocol, simulated clock, ground-truth log) | `scadavt/wds/server.py`, client `scadavt/wds/client.py` | `tests/test_wds_server.py` |
| Deployment generator: 14 field LANs, WAN backbone, control LAN, back channel, 15 routers, 33 PLC nodes, MTU, gateway, plant | `scadavt/deploy/compose.py` | 5 full deployments, `results/network/netcheck.json` |
| Node and router images (routing, shaping, capture) | `deploy/Dockerfile.net`, `deploy/net/*.sh` | same |
| Configuration files in the paper's format (IOModuleGate files 1 and 2, MTU address file, tag table, address plan) | written by the generator into `build/<name>/config/` | `tests/test_deploy.py` |
| Network check (paths, round-trip times, isolation, shaping, memory) | `scadavt/deploy/netcheck.py` | `results/network/` |
| Conformance test of a field-device program | `scadavt/deploy/conformance.py` | run against your PLC program |
| Experiment runner with network faults (link loss, rate limit, router freeze) and hooks | `scadavt/experiments/networked.py` | `experiments/smoke_validate.yaml` |
| Message codecs: IOModules, WDS, Modbus/TCP client | `scadavt/protocols/` | `tests/test_core.py` |
| Supervisory logic of the MTU | `scadavt/master/control.py` (`Supervisor`) | `tests/test_core.py` |

## 2. Address plan and ports

| Network | Subnet | Members |
|---|---|---|
| WAN backbone | 10.100.0.0/24 | control router .2; field router *k* at .(10+*k*); .200–.250 free for extra hosts |
| Field site *k* (A=1 … N=14) | 10.10.*k*.0/24 | router .254; PLCs .11, .12, … |
| Control LAN | 10.200.0.0/24 | router .254; MTU .10 |
| Back channel | 172.16.0.0/16 | PLC*n* 172.16.1.*n*; gateway .0.10; WaterSystem server .0.20; MTU .0.30 |

Ports: Modbus/TCP **502** on every PLC; I/O modules **9161** on every PLC (back
channel only); WaterSystem server **9000**; MTU's HMI server **5020** (back channel).

Inside every node: `/config` (read-only, generated files), `/logs` (shared with
the host as `build/<name>/logs`), environment `SCADAVT_NODE` (e.g. `PLC6`).
The scenario is available with `scadavt.scenario.load()`.

## 3. Program 1 — field device (`scadavt.field.plc`)

Command (from the `full` profile of the generator; change it there if your
program has another name):

    python -m scadavt.field.plc --name PLC6 --modbus 0.0.0.0:502 --io 0.0.0.0:9161

Behaviour:

1. Load the register map of the device: `scadavt.scenario.load().plcs[name].tags`.
   Each tag has `reg_type` (`A` input register, `H` holding register,
   `D` discrete input, `C` coil), `address` (zero-based on the wire) and
   `direction` (`i` sensor, `o` actuator). Real values are IEEE-754 float32 in
   two registers, high word first (`scadavt.protocols.modbus.float_to_regs`).
2. Serve Modbus/TCP, unit identifier 1, function codes 1, 2, 3, 4, 5, 6, 15, 16.
3. Serve the IOModules protocol (`scadavt/protocols/iomodules.py`):
   * write (function 2) of an `i` tag stores the value in the tag's input
     register or discrete input, and the reply echoes the transaction number;
   * read (function 1) of an `o` tag returns the last value the MTU wrote to the
     tag's holding register or coil, with the request's transaction number;
     if the MTU has **not yet written** that output, reply with transaction 0;
   * an unknown process ID, or a read of an `i` tag, gets transaction 0;
   * function 0 ends the session.
4. Keep no control logic of its own (the original SCADAVT PLCs are passive).

Check it before deploying:

    python -m scadavt.field.plc --name PLC6 --modbus 127.0.0.1:5020 --io 127.0.0.1:9161 &
    python -m scadavt.deploy.conformance --name PLC6 --modbus 127.0.0.1:5020 --io 127.0.0.1:9161

All checks must pass for every PLC (`for n in $(seq 1 33)`).

## 4. Program 2 — MTU (`scadavt.master.mtu`)

    python -m scadavt.master.mtu --plcs /config/mtu_plcs.ini --hmi 0.0.0.0:5020 \
        --historian /logs/historian.csv --connections /logs/connections.csv --period 1.0

Optional flag for experiment E1: `--no-v1-control` (V1 keeps its fixed setting).

Behaviour, every `--period` wall seconds (one cycle):

1. For every PLC that holds a tag in `Supervisor.required_reads()`, open a
   Modbus/TCP connection (`scadavt.protocols.modbus.ModbusClient`, connect
   timeout 5 s), read the tags, close. Poll the PLCs concurrently, so that one
   unreachable PLC does not delay the others.
2. Call `Supervisor.decide(values)` with only the values that were read; a
   missing value means "keep the previous decision" (already implemented).
3. Write each returned `Command` to its PLC (coil for status tags, two holding
   registers for real values). Write every cycle, as a polling SCADA master does.
4. Log every connection to `connections.csv` with the columns
   `wall_time, cycle, plc, op, ok, connect_ms, request_ms, error`
   (`op` is `read` or `write`; `ok` is 1 or 0).
5. Log every cycle to `historian.csv`: `wall_time, cycle`, one column per value
   read (`PLC1.T2_LVL`, …) and one per command (`cmd.PLC6.V1_SET_O`, …).
6. Optional: an HMI server (Modbus/TCP slave on `--hmi`) that exposes the values
   and accepts operator overrides, so that an HMI client on the host can connect
   through the back channel (172.16.0.30:5020).

## 5. Program 3 — IOModuleGate and meta-converter (`scadavt.gateway.bridge`)

    python -m scadavt.gateway.bridge --plcs /config/iomodulegate_plcs.ini \
        --tags /config/iomodulegate_tags.ini --wds 172.16.0.20:9000 --period 1.0 \
        --log /logs/gateway.csv

The two INI files are the configuration files of the paper. File 1 has one
section per PLC with `ip` and `port` on the back channel. File 2 has one section
per process parameter with `controller`, `paraType` (`i`/`o`), and, added for the
WDS protocol, `element`, `elementType` (`node`/`link`) and `dataType`
(a name from `scadavt.protocols.wdsproto.DATA_TYPE_CODES`).

Behaviour:

1. `IOModuleGate` class with `write(dict)` (process ID → value, sent to the
   right PLC as IOModules writes) and `read() -> dict` (all `o` parameters
   whose reply transaction is not 0). Keep one connection per PLC; number
   reads and writes separately, starting at 1.
2. Meta-converter loop every `--period` seconds: acquire every `i` parameter
   from the WaterSystem server (`scadavt.wds.client.WDSClient.acquire`), call
   `write`, call `read`, and send every output whose value changed since the
   last cycle to the server (`WDSClient.control`).
3. Log one row per cycle to `gateway.csv`:
   `wall_time, sim_h, cycle, n_inputs, n_outputs, n_controls, cycle_ms, errors`.

## 6. Running the experiments

    docker build -t scadavt:2.0 . && docker build -t scadavt-net:2.0 -f deploy/Dockerfile.net .
    python -m scadavt.deploy.netcheck --repeats 5                    # network only
    python -m scadavt.experiments.networked experiments/e1_supervised.yaml
    python -m scadavt.experiments.networked experiments/e3_plc6_link.yaml

The runner writes `results/networked/<name>/summary.json` with the same
measures as the process-level suite, plus the plant's ground truth, the MTU
logs, the gateway log, the fault actions with their wall and simulated times
(labels) and, with `capture: true`, one pcap per router.

Timing: with `step: 300` and `tick: 1.0` the plant runs 300 times faster than
real time, so one simulated week takes about 34 minutes, and one MTU cycle of
1 s covers 5 simulated minutes, the control period of the process-level runs.
Fault actions take effect within one polling interval (0.2 s wall, 1 simulated
minute at this speed).

## 7. Attacks

The runner's `hook` fault calls your own commands at the start and end of a
window. The repository does not contain attack tools; the experiments in the
paper model faults by their physical consequence (process-level mode) or by
network faults (link loss, rate limit, router freeze) in the networked mode.
