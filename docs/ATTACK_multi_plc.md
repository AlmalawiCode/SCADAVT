# Multi-PLC attack scenarios (live CORE, domain-neutral)

Built on the verified multi-PLC centralized-control rig (`tools/live_core_multiplc.py`:
one MTU, three network-separated PLCs, cross-PLC rules). An attacker node sits on
the SCADA network. Tool: `tools/live_core_multiplc_attack.py --scenario {dos,inject}`.
No EPANET; sensors seeded (S1=2 low → A2 open; S2=5 high → A3 off; S3=2 low → A1 open).

## Scenario `dos` — communication disruption + failure isolation (7/7)
- **Application-layer flood** (`scadavt.attacks.dos`, 128 workers, ~280k requests):
  **honest finding** — on this fast host the robust asyncio field device *absorbed*
  the flood (short-deadline read fail-rate on PLC2 = 0.00). A single-event-loop or
  slower device would miss deadlines; ours does not here.
- **Network-layer disruption** of the MTU↔PLC2 path (netem 100% loss on PLC2's
  link — models jamming/link saturation): **deterministic and verified** —
  - PLC2 monitoring LOST: MTU short-deadline reads of PLC2 fail-rate = **1.00**;
  - **failure isolation**: PLC1 & PLC3 reads keep succeeding (fail-rate **0.00**),
    and PLC1's actuator stays correctly controlled (A1 open) throughout;
  - **recovery**: PLC2 monitoring restored (fail-rate 0.00) once the link clears.

## Scenario `inject` — cross-PLC command manipulation + isolation (6/6)
- An unauthorized Modbus writer (`scadavt.attacks.modbus_inject`, ~1093 frames to
  PLC2:502) **forces PLC2's actuator to 0**, overriding the MTU's legitimate
  cross-PLC OPEN command (which the rule derived from PLC1's reading).
- **Isolation**: PLC1's actuator stays rule-correct (A1 open) during the attack —
  the manipulation is localized to the targeted PLC.

## Evidence
`<tmp>/scadavt_mplcatk_*/evidence/` (attack pcap for inject); attacker logs
`/tmp/dos.csv`, `/tmp/inj.csv`. Unit suite 112 passed, 1 skipped.

## Follow-ups
- A true cross-PLC *sensor* attack (MITM-falsify PLC1's reading so the MTU
  misactuates PLC2) — needs targeted MITM placement on the MTU↔PLC1 path.
- Hybrid command ownership/priority (design) before deployable local PLC control.
