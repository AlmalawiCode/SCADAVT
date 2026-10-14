# Live attack #1 — Modbus command injection (unauthorized competing writes)

**Classification.** This experiment is **Modbus command injection**, i.e.
*unauthorized competing writes* — **not** an in-transit man-in-the-middle (MITM).
The attacker is a **separate, independent Modbus master** on the SCADA network
that opens its **own** TCP connection to the PLC and writes the command register
directly. It does **not** intercept, relay, or alter the legitimate MTU's frames;
it simply **competes** with them, and the register ends up holding whichever
master wrote last (last-writer-wins). The legitimate MTU↔PLC connection is
untouched and keeps running in parallel.

- MITRE ATT&CK for ICS: **T0855** (Unauthorized Command Message), **T0836** (Modify Parameter).
- Enabling weakness: Modbus/TCP has **no authentication** — any host that can
  reach `PLC:502` may write its coils/holding registers.
- Contrast with the planned experiment #2 (in-transit MITM), where the attacker
  ARP-poisons the path and **rewrites the MTU's own write frame** in transit, so
  there is one write (the MTU's, altered) rather than two competing writers.

## Attacker assumptions
- Positioned **on the SCADA L2 segment** (node `ATK`, `10.0.0.9`) with IP
  reachability to the PLC's Modbus port (`10.0.0.1:502`). No routing off-segment.
- **No credentials** (Modbus/TCP is unauthenticated).
- Knows the target **command register address**. In this harness the address is
  read from the session register map for convenience; a real attacker would
  **reverse-engineer it** from captured traffic (the same `func 0x10` writes to
  address `0` are visible in the PCAP) or by enumeration.
- Writes a fixed malicious value (`P1_SPD = 0.0`, pump forced off). The attack
  logic is open-loop (it does not read state back).

## The 300 Hz write rate (why)
The register is **last-writer-wins**. The legitimate MTU writes at ~**10 Hz**
(`master --step 0.1`). The gateway samples the register (to apply it to EPANET)
once per control cycle. To make the malicious value the one present at (almost)
every gateway read, the attacker writes much faster — **~300 Hz** — so it
dominates the race: measured **90–100 %** of sampled instants hold `0.0`.
This is a deliberate experiment parameter, **honestly a race**: at lower rates
the attacker wins a smaller fraction and the physical impact is weaker/variable
(observed directly during development). The rate is a knob (`--hz`), not a
guarantee; the zero-fraction is reported rather than assumed.

## Simulation time vs wall-clock
Two independent clocks run here, and the experiment spans both:
- **Wall-clock:** the attacker (~300 Hz), the MTU master (~10 Hz) and the gateway
  loop all run in real asyncio time. The *competition at the register* happens in
  wall-clock, and the attack window is specified in wall-clock seconds.
- **Simulation time:** EPANET only advances when the **gateway calls STEP**
  (120 **simulated** seconds per gateway cycle). So the hydraulic consequence
  accrues in simulation time as the gateway steps, driven by whatever value the
  register holds at each step.

Consequence for the harness: the gateway/master loop **must keep stepping for the
whole attack window** (here `cycles = 140`, ≈14 s wall-clock). If the loop ends
while the attacker is still writing, EPANET **freezes** and the attack appears
ineffective — this was a real bug found and fixed. Because the two clocks are
decoupled, exact wall-clock timing is **not reproducible**, but the *logical
outcome* (register forced to 0 → pump off → tank drains) is, which is why the
results are read as *reproducible in effect*, not bit-identical.

## Independently captured evidence (each at its own stage/node)
| Value / artifact | Where it is read | Node |
| --- | --- | --- |
| **commanded** | the MTU master's own decision log (`CMD` lines) | MTU |
| **received** | a Modbus read of the PLC command register (`probe`) | MTU → PLC |
| **applied** | EPANET's actual pump setting via the Integration API (`P1_SET`) | APP |
| **tank level** | EPANET via the Integration API (`T1_LEV`) | APP |
| **PCAP** | `tcpdump` on the PLC's SCADA interface (`eth0`) | PLC |
| **attacker writes** | the attacker's own timestamped CSV | ATK |

These are genuinely independent: commanded comes from the controller, received
from the field register over Modbus, applied from the physics engine — so their
divergence is observed, not assumed. The PCAP decode shows the attacker's frames
are Modbus **function `0x10` (write-multiple-registers)**, value float `0.0`,
from `10.0.0.9` to `10.0.0.1:502`.

## Verified result (2 reproducible runs, live CORE)
- Benign: commanded = received = applied = `0.7`; tank holds ~3–4.5 m.
- Attack: commanded `0.7`→`1.0` (controller escalates to recover) while
  received + applied are forced to `0` for ~90–100 % of the window; tank
  **empties (~0 m)**; ~3500 attacker `func 0x10` frames on the wire.
- Causality: the physical drop coincides with the attacker's on-wire frames;
  EPANET and the gateway are unmodified.

Reproduce: `SCADAVT_DIR=$PWD SCADAVT_NODE_PY=$PWD/.venv/bin/python /opt/core/venv/bin/python tools/live_core_attack.py`
(needs a running `core-daemon` and `numpy`+`epanet` in the node python).
