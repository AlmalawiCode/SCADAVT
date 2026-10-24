# Generic N-PLC network API (live-validated)

The framework's headline capability: a user builds an **arbitrarily large** SCADA
network of PLCs **programmatically in Python** — give each PLC an IP, wire them
through switches and routers, and run them under one centralized MTU with
cross-PLC control — with **no per-scenario code**. The drawing UI is a separate,
later concern; this is the engine beneath it.

## The API — `scadavt.core.corelive`

```python
from core.api.grpc import client
from scadavt.core import corelive

core = client.CoreGrpcClient(); core.connect()

cfg = corelive.grid(12, per_subnet=6)   # or hand-write any coregen config dict
net = corelive.build(core, cfg)          # create session + nodes + links (any N)
net.start()                              # start CORE, bring up WAN routing, launch programs
net.ping("MTU", "10.0.2.1")              # connectivity
net.probe("MTU", "PLC7", "s")            # Modbus read a point across the network
net.probe("MTU", "PLC7", "a", write=1)   # Modbus write a command point
net.stop()                               # tear the session down
```

* **`build_plan(cfg)`** — pure, CORE-independent: resolves any config into the
  exact ordered node/link plan (interface indices, per-subnet IPs, router
  gateways). Unit-tested offline in `tests/test_corelive.py` for N = 1..50.
* **`build(core, cfg)`** — creates the live CORE session and adds every node and
  link by iterating the plan; deletes the session if construction fails.
* **`LiveNetwork.start()`** — `start_session`, enables IP forwarding on routers
  and a default route on every endpoint (WAN bring-up), then launches the real
  PLC device program inside every field node and the control master on the MTU.
* **`grid(n, per_subnet=...)`** — convenience: N PLCs across as many /24 subnets
  as needed, joined by one router, one MTU, and a cross-PLC control ring.

Each PLC is a real CORE Linux-namespace node with an IP on its interface,
running the actual Modbus device; a switch is one LAN; a router joins subnets
into a WAN; per-link `delay`/`loss`/`bandwidth` is real `netem`.

## Live validation (real CORE daemon)

`tools/live_core_network.py` is the user-facing demo/regression. Each scenario
verifies, for every PLC: IP connectivity from the MTU, a real Modbus
request/response from the MTU (application-layer proof), a per-PLC pcap
(wire-level proof on the MTU interface), and the cross-PLC control relationships.

Run on `abdul-Alienware-x17-R2`, CORE 9.x daemon, host load avg ~18:

| Scenario | Topology | Result | Wall |
|----------|----------|--------|------|
| `--plcs 3` | 3 PLCs, 1 subnet | **7/7 PASS** | 15 s |
| `--manual` | 4 PLCs, 2 subnets via router, non-ring rules | **8/8 PASS** | 17 s |
| `--plcs 12 --per-subnet 6` | 12 PLCs, 2 subnets via router (WAN) | **11/11 PASS** | 29 s |

Highlights:

* **12/12 PLCs reachable and answering Modbus** across a routed WAN.
* **Cross-PLC control fires across subnet boundaries** — in the manual case,
  PLC3 (subnet B) opens PLC2 (subnet A), the reverse direction across the
  router; non-triggered actuators (A1, A4) correctly stay off (no spurious
  activation).
* Orchestrator RSS ~41 MB; the same code path runs 3 or 60 PLCs.

### Reproduce

```bash
sudo systemctl start core-daemon        # once, needs root
cd scadavt
SCADAVT_DIR="$PWD" SCADAVT_NODE_PY="$PWD/.venv/bin/python" \
  /opt/core/venv/bin/python tools/live_core_network.py --plcs 12 --per-subnet 6
# non-grid topology:
SCADAVT_DIR="$PWD" SCADAVT_NODE_PY="$PWD/.venv/bin/python" \
  /opt/core/venv/bin/python tools/live_core_network.py --manual
# offline plan tests (no daemon):
.venv/bin/python -m pytest tests/test_corelive.py -q
```

### Notes / limitations

* Under extreme host load, wire-level pcap capture needs a clean drain + SIGINT
  (handled in `capture_stop`); the authoritative communication proof is the
  per-PLC Modbus round trip, which does not depend on capture timing.
* Centralized (MTU) control is validated here. Deployable local-PLC control and
  MTU-loss failover remain follow-ups.
