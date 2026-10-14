# Regression — centralized multi-PLC / cross-PLC MTU control (live CORE)

Validates the requirement that **one MTU reads measurements from several
network-separated PLCs, evaluates cross-PLC rules, and commands actuators on
other PLCs** — the basis for realistic multi-PLC attack experiments.

Tool: `tools/live_core_multiplc.py --topo {lan,wan,both}` (domain-neutral, no
EPANET — exercises the generic framework). Three PLCs, each a sensor + actuator;
sensors seeded (S1=2 low, S2=5 high, S3=2 low). The MTU master (`scadavt.core.master`)
drives all three with a **cross-PLC ring**: S1(PLC1)→A2(PLC2), S2(PLC2)→A3(PLC3),
S3(PLC3)→A1(PLC1).

## Verified live (16/16: 8 LAN + 8 WAN)
- **LAN** (all on one switch) and **WAN** (MTU+PLC1 on subnet A; PLC2/PLC3 on
  subnet B via a `model="router"` node) — MTU reaches every PLC.
- **MTU communicated Modbus with all 3 PLCs** (pcap on the MTU interface, per-PLC).
- **Cross-PLC control correct:** A2(PLC2)=OPEN from PLC1's low reading; A1(PLC1)=OPEN
  from PLC3's low reading; A3(PLC3)=OFF because PLC2's reading is high — each
  actuator commanded from *another* PLC's measurement, read back over Modbus.
- Per-PLC links are read/written in bounded try/except, so one PLC does not stall
  the others (relevant to communication-disruption experiments).

Evidence per run: `<tmp>/scadavt_mplc_*/evidence/{lan,wan}_mtu.pcap`.

## Not covered here (follow-ups)
- Physical closed-loop feedback across multiple PLCs (sensors are seeded here, not
  driven by a live multi-element process — that is the multi-tank EPANET / `town`
  path, deferred).
- Multi-PLC *attack* scenarios (DoS/MITM on MTU↔PLC2 while PLC1 continues; cross-PLC
  injection) — next recommended step.
- Command ownership/priority for hybrid MTU + local-PLC control (design pending).
