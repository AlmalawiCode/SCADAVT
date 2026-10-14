# Live attack #2 — in-transit Modbus integrity (MITM command rewrite)

**Classification.** Genuine **man-in-the-middle (MITM) integrity attack**: the
attacker gets *on the path* between the MTU and the PLC and **rewrites the MTU's
own write frame in transit** — there is **one** command stream (the MTU's) whose
value is altered between sender and receiver. This is different from experiment
#1 (command injection = a *second* competing writer); see
[`ATTACK_command_injection.md`](ATTACK_command_injection.md).

Built on the repo's existing MITM framework (unchanged logic):
`attacks/arp.py` (ARP cache poisoning), `attacks/proxy.py` (transparent,
Modbus-aware TCP proxy via `iptables REDIRECT` + `SO_ORIGINAL_DST`),
`attacks/handlers.py:CommandHandler` (rewrites FC5/6/16 write requests),
`attacks/mitm.py` (the agent). The **physical simulator (EPANET) and the gateway
are unchanged.** MITRE ATT&CK for ICS: **T0856/T0836** (Spoof Reporting /
Modify Parameter) on the command path.

Topology: an `ATK` node (`10.0.0.9`) on the SCADA LAN ARP-poisons the MTU
(`10.0.0.254`) and the PLC (`10.0.0.1`), REDIRECTs the intercepted `*:502`
connection into the proxy, which relays frames and — while the attack window is
open — rewrites the pump-speed write to `0.0` with `CommandHandler("H", 0,
"const", 0.0)`.

## L3-ROUTED variant — CLEAN cyber-physical MITM (VERIFIED, `tools/live_core_mitm_l3.py`)

The ARP variant below disrupted the MTU's persistent connection. The **L3-routed**
variant fixes that: the MTU and PLC are on two SCADA subnets and the **attacker is
the router between them**, so MTU↔PLC traffic is forwarded through it by routing
(no ARP poisoning). The attacker REDIRECTs the transit `:502` connection into the
proxy and rewrites with `CommandHandler("H", 0, "const", 0.0)` while the window is
open. EPANET, the gateway, the scenario **and the MTU's normal persistent-connection
control behaviour** are all unchanged (the back channel stays off the routed path).

**Verified live on CORE (2 reproducible runs, 7/7), with the clean result claimed
only because all of the following held together:**
- **Connection stable, not disrupted.** Through the whole attack the master kept
  its persistent session and kept writing — **0** `socket.send` exceptions and
  **77 MTU-side writes** on the wire. (This is the explicit *modification vs
  disruption* discriminator.)
- **Command modified in transit (both-interface PCAP).** Benign: MTU-side `{0.7,
  1.0}` == PLC-side `{0.7, 1.0}`. Attack: MTU-side `{0.7, 1.0}` (**never 0**) but
  PLC-side carries **`0.0`** — a value the sender never put on the wire, so it was
  altered between the two interfaces. Write counts match (77 = 77): the stream
  flows, it is not dropped.
- **Three-way divergence:** received and applied forced to `0` for 100 % of the
  sampled window while the MTU commands `0.7`→`1.0`.
- **Independently measured hydraulic impact:** benign tank holds ~2.9 m; under the
  attack the tank **empties (~0 m)** — with a stable connection in both phases, so
  the drop is the effect of the *modification*, not of a broken link.

Evidence per run (`evidence/`): `benign_mtu.pcap`/`benign_plc.pcap`,
`attack_mtu.pcap`/`attack_plc.pcap`, `benign_trace.csv`/`attack_trace.csv`
(level + applied + received), `mitm_agent.csv`, `commanded_mtu.log`.
Reproduce: `SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python /opt/core/venv/bin/python tools/live_core_mitm_l3.py`.
A small `--no-arp` mode was added to `attacks/mitm.py` so the agent skips ARP and
only REDIRECTs (the routing already puts it on-path). Single PLC.

## (ARP variant) What is VERIFIED live on CORE
- **The in-transit rewrite works.** With the MITM path up and the window open, a
  **legitimate Modbus write of `0.7`, sent through the path, is received as `0.0`
  at the PLC** (confirmed by reading the register back: `wrote 0.7 -> read 0.0`),
  and the agent records a rewrite **hit**. The command was altered on the wire,
  not in EPANET or the gateway.
- **The MITM position is real.** The transparent proxy relays the intercepted
  Modbus traffic to the PLC (its upstream `10.0.0.9 -> 10.0.0.1:502` connection is
  visible in the PCAP), and the agent logs `mitm_up` then `start`.
- Reproducible via
  `SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python /opt/core/venv/bin/python tools/live_core_mitm.py`
  (3/3 verified checks). Loopback unit tests already cover `CommandHandler`, the
  proxy round-trip and an end-to-end MITM (`tests/test_attacks.py`).

## What is NOT verified (honest limitation)
- **The continuous closed-loop impact under the *live master* is not cleanly
  demonstrated.** The MTU master (`scadavt.core.master`) holds **one long-lived
  Modbus connection** and reuses it. Through the ARP-MITM in the CORE bridged
  segment that connection is **unstable** — the master logs repeated
  `socket.send() raised exception`, and its continuous reads/writes do **not**
  flow through the proxy (no `FC16`/`FC4` from the master appear on either
  interface; only short-lived fresh connections — the probe and the isolated
  test write — relay reliably). Consequently:
  - the both-interfaces PCAP comparison (MTU-side `0.7` vs PLC-side `0.0`) could
    **not** be captured for the master's stream;
  - the tank *did* drain more during the attack window, but with the master's
    connection unstable in **both** phases the hydraulic difference is
    **confounded** (a connectivity side-effect), so it is **not** reported as a
    clean cyber-physical impact of the rewrite.

## Root cause & recommended next steps
- Root cause: ARP-MITM interception of a **persistent** TCP Modbus session in a
  CORE bridge is fragile (the established flow is disrupted when interception
  engages), whereas short connections are intercepted cleanly. The rewrite logic
  itself is correct (verified on a fresh write).
- Next steps to get a clean closed-loop MITM impact:
  1. make the master tolerant of a MITM'd session (reconnect-per-cycle, or a
     short keep-alive) so each intercepted connection is fresh and stable; or
  2. intercept at L3 with a routed path (attacker as the gateway between two
     subnets) instead of L2 ARP, so the proxy sees clean new connections; and
  3. capture on both MTU and PLC interfaces to show the per-frame `0.7 -> 0.0`
     divergence for the live control stream.
- This is a single-PLC experiment; not scaled to the multi-PLC network.
