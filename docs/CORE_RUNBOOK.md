# Running SCADAVT on a live CORE host

This is the step-by-step for running a generated session on a machine where
**CORE can actually run** (Linux, root / namespace privileges, `core-daemon`).
The development sandbox cannot do this (no namespace privileges), which is why
the automated tests use loopback; everything below is the real-emulation path.

The flow has four moving parts, exactly as in the thesis:

```
 external app (your simulator) --Integration API (back channel)--> gateway
 gateway --IOModules (back channel)--> PLC/RTU nodes <--Modbus (SCADA net)--> MTU
 CORE emulates the SCADA net (nodes, links, delay/loss); you capture pcaps there.
```

## 0. Prerequisites
- CORE installed and the daemon running: `sudo core-daemon` (or the service).
- A Python env with this package importable and `epanet` if you use the water
  example. Confirm: `python -c "import scadavt.core, epanet"`.
- `tcpdump`, and (for attacks) `hping3`.

## 1. Write a config and generate the session
A config is plain data (see `scadavt/examples/demo.py: demo_config()` or any of
the test configs). Generate the artefacts:

```python
from scadavt.core import coregen
cfg = {...}                      # your devices / links / mapping / control
assert coregen.validate(cfg) == []
coregen.write_session(cfg, "run/")      # -> run/session.json, gate_*.ini,
                                        #    core_session.py, run_devices.sh
```

## 2. Start the emulated SCADA network in CORE
`core_session.py` builds the topology the way CORE itself models it
(`core.api.grpc`, CORE 9.x):

- each PLC/RTU/MTU is a **Linux-namespace node** (`model="PC"`) with an
  **explicit IP on its interface** — `wrappers.Interface(id, ip4=..., ip4_mask=24)`,
  taken straight from the config, so *every node has its own IP*;
- a **SWITCH** node makes a **LAN**: all nodes linked to it share a subnet and
  reach each other directly (e.g. `MTU 10.0.0.254` ↔ `PLC1 10.0.0.1`);
- a **`model="router"`** node (OSPF/IPForward) joins two subnets into a **WAN**
  (e.g. `PLC1 10.0.0.0/24` ↔ router ↔ `PLC2 10.0.1.0/24`);
- per-link delay/loss/bandwidth are real **`netem`** via `LinkOptions`.

Point the script at the repo (shared node filesystem) and run it against the
daemon:

```bash
export SCADAVT_DIR=/path/to/scadavt        # repo root, reachable inside nodes
cd run && python core_session.py           # creates + starts nodes and links
```

`coregen.address_plan(cfg)` prints the full IP plan (which node has which IP on
which LAN) before you run.

## 3. The node programs start *inside* their namespaces automatically
After `start_session`, the script launches each node's own program **in-namespace**
with `node_command` — no manual step:

- every field node runs the device runner (`scadavt.core.device`): a Modbus
  slave on the SCADA net + an I/O-modules server on the back channel;
- the MTU node runs the master (`scadavt.core.master`): it reads each PLC's
  measurement registers over Modbus, runs the rule engine, and writes the
  command registers back — the centralized control loop, node to node.

Logs land in `/tmp/<NODE>.log` inside the nodes. `run_devices.sh` lists the exact
commands if you prefer to launch them by hand (or attach them as CORE services).
To run a program in a node yourself: `core.node_command(session.id, node.id, cmd)`
(gRPC) or the node's shell via `vcmd`.

## 4. Start your external application (outside CORE)
Any program that speaks the Integration API. The water example:

```python
import asyncio
from scadavt.examples.water_app import WaterApp
async def main():
    app = WaterApp()
    server = await app.serve("0.0.0.0", 9000)      # LISTENS; the gateway connects
    await server.serve_forever()
asyncio.run(main())
```

For another domain, subclass `scadavt.core.integration.ExternalApp` (see
`examples/dummy_app.py`) — no change to SCADAVT.

## 5. Run the gateway (on the host, reaching node back channels)
```python
import asyncio, json
from scadavt.core import integration as ig
from scadavt.core.gateway import Gateway
async def main():
    session = json.load(open("run/session.json"))
    app = ig.IntegrationClient("127.0.0.1", 9000)   # your external app
    await app.hello()
    gw = Gateway.from_session(session, app)         # reads node back channels
    await gw.run(cycles=0 or a_large_number)        # one cycle per control step
asyncio.run(main())
```

Control runs where the config places it: in the **MTU** node (centralized), in a
**PLC** node (local, start a control host there), or **distributed**.

## 6. Inject a disturbance / attack (on the SCADA net, inside CORE)
These act on the emulated SCADA traffic — the scientifically valid surface:

- **Delay / loss**: set per-link `netem` (already applied from the config, or
  change it live in CORE).
- **DoS**: from an attacker node, `hping3 --flood -S -p 502 <plc-ip>`.
- **Integrity / MITM**: insert a proxy on the MTU↔PLC path (see
  `scadavt/attacks/`), altering Modbus writes in flight.

Record the window (kind, target, start, end) for the labels.

## 7. Capture and export a bundle
Capture per node/interface (needs privileges), then assemble the bundle:

```python
from scadavt.core.capture import PcapCapture
cap = PcapCapture("run/plc1.pcap", iface="eth0", bpf="tcp port 502",
                  node_cmd=["vcmd", "-c", "/tmp/pycore.<id>/PLC1"])   # inside the node
cap.start();  ...run the experiment...;  cap.stop()
```

```python
from scadavt.core.bundle import ExperimentRecorder
from scadavt.core import labels as lbl
rec = ExperimentRecorder(metadata={...})          # config, seeds, timing, versions
# record (t, {measurements/commands/states}) each cycle on the reference clock
rec.windows = [lbl.AttackWindow("dos", "PLC1", start=..., end=...)]
rec.confirm(t, "src=attacker")                    # confirmed-malicious evidence
rec.pcaps = ["run/plc1.pcap"]
rec.write_bundle("run/bundle")                    # trace.csv + labels.csv + metadata.json + pcaps
```

The bundle timestamps the pcaps, the process trace and the two-dimensional
labels on one clock, so the dataset is ready for intrusion-detection research.

## Notes / current gaps
- There is no single packaged "launch everything" CLI yet; steps 3–5 are run as
  shown. Wrapping them into one launcher is a small, worthwhile next step.
- `simpletcp` is a reference second field-bus; real DNP3/IEC-104 are future
  plugins behind the same interfaces.
- For a quick, CORE-free tour of the moving parts, run
  `python -m scadavt.examples.demo`.
