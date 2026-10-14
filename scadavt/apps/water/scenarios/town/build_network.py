"""Build the EPANET description file of the small-town water distribution system.

The town has six supply areas (A-F) and two treatment works. Each area is fed
by an elevated tank (T1-T6) and contains three or four sub-areas. Every
sub-area is a junction on the area main, a pressure-reducing valve (the
sub-area valve) and a demand node that represents the houses of the sub-area.
The flow through the sub-area valve is what the flow meter of the sub-area
measures.

    Works 1 --P1,P2,P3--> T1 --V3b--> area A
                          T1 --P4,P5--V35b--> T2 --V35a--> area B
                                              T2 --V1 (flow control)--> T3 --V7--> area C
    Works 2 --P8,P9--V21a--> T4 --V21b--> area D
                             T4 --P10,P11--V26b--> T5 --V26a--> area E
    Works 2 --P6,P7--V33b--> T6 --V33a--> area F
                             T6 --P12--> T3   (support of area C from works 2)

Run this file to regenerate ``town.inp``; the result is committed so that
users can open and edit it in the EPANET graphical interface.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent

# --------------------------------------------------------------------------
# Sub-areas: id, area, sub-area valve id, population (from the original
# SCADAVT scenario), ground elevation (m), layout coordinates
# --------------------------------------------------------------------------
SUBAREAS = [
    # area A, fed by T1
    ("A1", "A", "V5", 4500, 22), ("A2", "A", "V8", 5200, 20),
    ("A3", "A", "V9", 5000, 18), ("A4", "A", "V10", 3500, 16),
    # area B, fed by T2
    ("B3", "B", "V11", 6500, 30), ("B2", "B", "V12", 3500, 28),
    ("B1", "B", "V13", 6000, 26),
    # area C, fed by T3
    ("C1", "C", "V14", 3600, 22), ("C2", "C", "V15", 4999, 20),
    ("C3", "C", "V16", 2560, 18), ("C4", "C", "V17", 3256, 16),
    # area D, fed by T4
    ("D1", "D", "V18", 6800, 20), ("D2", "D", "V19", 4600, 18),
    ("D3", "D", "V20", 3600, 16),
    # area E, fed by T5
    ("E1", "E", "V22", 3560, 26), ("E2", "E", "V23", 4500, 24),
    ("E3", "E", "V24", 3690, 22),
    # area F, fed by T6
    ("F1", "F", "V32", 3600, 20), ("F2", "F", "V31", 4300, 18),
    ("F3", "F", "V30", 3600, 16),
]

# Area main valve (tank outlet) and supplying tank
AREA_FEED = {"A": ("T1", "V3b"), "B": ("T2", "V35a"), "C": ("T3", "V7"),
             "D": ("T4", "V21b"), "E": ("T5", "V26a"), "F": ("T6", "V33a")}

# Tanks: elevation (m), initial / minimum / maximum level (m), diameter (m).
# T1 has twice the storage of T2 and T3, as in the original scenario.
TANKS = {
    "T1": (62.0, 4.0, 0.2, 7.0, 19.8),
    "T2": (78.0, 3.5, 0.2, 7.0, 14.0),
    "T3": (64.0, 3.5, 0.2, 7.0, 14.0),
    "T4": (60.0, 4.0, 0.2, 7.0, 26.0),
    "T5": (72.0, 3.5, 0.2, 7.0, 20.0),
    "T6": (60.0, 4.0, 0.2, 7.0, 24.0),
}

# Pumps: id, group from-node, to-node, design flow (L/min), design head (m)
PUMPS = [
    ("P1", "J_W1", "J_P123", 3200.0, 55.0),
    ("P2", "J_W1", "J_P123", 3200.0, 55.0),
    ("P3", "J_W1", "J_P123", 3200.0, 55.0),
    ("P4", "J_T1o", "J_P45", 1900.0, 20.0),
    ("P5", "J_T1o", "J_P45", 1900.0, 20.0),
    ("P6", "J_W2b", "J_P67", 2400.0, 52.0),
    ("P7", "J_W2b", "J_P67", 2400.0, 52.0),
    ("P8", "J_W2a", "J_P89", 3200.0, 52.0),
    ("P9", "J_W2a", "J_P89", 3200.0, 52.0),
    ("P10", "J_T4o", "J_P1011", 1900.0, 26.0),
    ("P11", "J_T4o", "J_P1011", 1900.0, 26.0),
    ("P12", "J_T6o", "J_P12", 1500.0, 24.0),
]

COORDS = {
    "W1": (2000, 1000), "J_W1": (2100, 1000), "J_P123": (2400, 1000),
    "T1": (2800, 1400), "J_T1o": (2700, 1700), "J_P45": (2400, 1900),
    "T2": (1700, 2100), "J_V1u": (2000, 2400), "J_V1d": (2200, 2600),
    "T3": (2200, 2800), "J_T3i": (2250, 2700),
    "W2": (4800, 2600), "J_W2a": (4800, 2700), "J_P89": (4800, 3100),
    "T4": (4200, 3400), "J_T4o": (4200, 3600), "J_P1011": (3900, 3900),
    "T5": (3500, 4200), "J_W2b": (4700, 2600), "J_P67": (4300, 2500),
    "T6": (3200, 2500), "J_T6o": (3000, 2550), "J_P12": (2600, 2650),
}


def _pump_curve_lines():
    lines = []
    for pid, _, _, q, h in PUMPS:
        lines.append(f" C_{pid:<8} {q:10.1f} {h:8.1f}")
    return lines


def build(path: Path = HERE / "town.inp") -> Path:
    junctions, reservoirs, tanks, pipes, pumps, valves = [], [], [], [], [], []
    coords = dict(COORDS)
    tags = []

    def junc(jid, elev, x=None, y=None, demand=0.0):
        junctions.append(f" {jid:<10} {elev:8.1f} {demand:10.3f}")
        if x is not None:
            coords[jid] = (x, y)

    def pipe(pid, a, b, length, diam, rough=120):
        pipes.append(f" {pid:<10} {a:<10} {b:<10} {length:8.1f} {diam:8.1f} {rough:6.1f} 0 Open")

    # works (reservoirs) -----------------------------------------------------
    reservoirs.append(" W1         20.0")
    reservoirs.append(" W2         20.0")

    # works 1 -> T1 ----------------------------------------------------------
    junc("J_W1", 10.0)
    junc("J_P123", 10.0)
    junc("J_V3a", 30.0, 2600, 1200)
    pipe("p_W1", "W1", "J_W1", 20, 600)
    pipe("p_P123", "J_P123", "J_V3a", 1200, 500)
    valves.append(" V3a        J_V3a      J_T1i      500 TCV 0 0")
    junc("J_T1i", 40.0, 2700, 1300)
    pipe("p_T1i", "J_T1i", "T1", 50, 500)

    # T1 -> T2 ---------------------------------------------------------------
    junc("J_T1o", 40.0)
    pipe("p_T1o", "T1", "J_T1o", 300, 500)
    junc("J_P45", 40.0)
    junc("J_V35b", 55.0, 2000, 2000)
    pipe("p_P45", "J_P45", "J_V35b", 1500, 400)
    valves.append(" V35b       J_V35b     J_T2i      400 TCV 0 0")
    junc("J_T2i", 70.0, 1800, 2050)
    pipe("p_T2i", "J_T2i", "T2", 50, 400)

    # T2 -> V1 -> T3 (gravity) -----------------------------------------------
    junc("J_V1u", 60.0)
    pipe("p_T2V1", "T2", "J_V1u", 1500, 350)
    junc("J_V1d", 60.0)
    valves.append(" V1         J_V1u      J_V1d      350 FCV 2000 0")
    junc("J_T3i", 62.0)
    pipe("p_V1T3", "J_V1d", "J_T3i", 200, 350)
    pipe("p_T3i", "J_T3i", "T3", 30, 400)

    # works 2 -> T4 and T6 ---------------------------------------------------
    junc("J_W2a", 10.0)
    junc("J_W2b", 10.0)
    pipe("p_W2a", "W2", "J_W2a", 20, 600)
    pipe("p_W2b", "W2", "J_W2b", 20, 600)
    junc("J_P89", 10.0)
    junc("J_V21a", 40.0, 4500, 3300)
    pipe("p_P89", "J_P89", "J_V21a", 1200, 450)
    valves.append(" V21a       J_V21a     J_T4i      450 TCV 0 0")
    junc("J_T4i", 55.0, 4300, 3350)
    pipe("p_T4i", "J_T4i", "T4", 30, 450)
    junc("J_P67", 10.0)
    junc("J_V33b", 40.0, 3700, 2500)
    pipe("p_P67", "J_P67", "J_V33b", 1200, 400)
    valves.append(" V33b       J_V33b     J_T6i      400 TCV 0 0")
    junc("J_T6i", 55.0, 3400, 2500)
    pipe("p_T6i", "J_T6i", "T6", 30, 400)

    # T4 -> T5 ---------------------------------------------------------------
    junc("J_T4o", 40.0)
    pipe("p_T4o", "T4", "J_T4o", 200, 400)
    junc("J_P1011", 40.0)
    junc("J_V26b", 60.0, 3700, 4100)
    pipe("p_P1011", "J_P1011", "J_V26b", 1000, 350)
    valves.append(" V26b       J_V26b     J_T5i      350 TCV 0 0")
    junc("J_T5i", 68.0, 3550, 4150)
    pipe("p_T5i", "J_T5i", "T5", 30, 350)

    # T6 -> P12 -> T3 --------------------------------------------------------
    junc("J_T6o", 40.0)
    pipe("p_T6o", "T6", "J_T6o", 100, 300)
    junc("J_P12", 40.0)
    pipe("p_P12", "J_P12", "J_T3i", 3500, 300)

    # pumps ------------------------------------------------------------------
    for pid, a, b, _, _ in PUMPS:
        pumps.append(f" {pid:<10} {a:<10} {b:<10} HEAD C_{pid}")

    # areas ------------------------------------------------------------------
    area_layout = {  # origin of the area main, direction of the main
        "A": ((3000, 1000), (450, 0)), "B": ((1400, 2300), (-300, 0)),
        "C": ((2400, 3100), (400, 0)), "D": ((4300, 3800), (0, 350)),
        "E": ((3300, 4500), (-350, 0)), "F": ((2900, 2900), (-50, 400)),
    }
    for area, (tank, mainv) in AREA_FEED.items():
        (ox, oy), (dx, dy) = area_layout[area]
        tx, ty = COORDS[tank]
        up = f"J_{mainv}"
        junc(up, TANKS[tank][0] - 5.0, (tx + ox) / 2, (ty + oy) / 2)
        pipe(f"p_{tank}{area}", tank, up, 150, 400)
        prev = f"J_{area}0"
        junc(prev, 35.0, ox, oy)
        valves.append(f" {mainv:<10} {up:<10} {prev:<10} 400 TCV 0 0")
        subs = [s for s in SUBAREAS if s[1] == area]
        for i, (sid, _, vid, _, elev) in enumerate(subs, start=1):
            jx, jy = ox + dx * i, oy + dy * i
            j = f"J_{sid}"
            junc(j, elev + 5.0, jx, jy)
            pipe(f"p_{area}{i}", prev, j, 800, 300)
            prev = j
            # sub-area valve (pressure reducing) and demand node
            junc(sid, elev, jx + 120, jy - 160, demand=500.0)
            junc(f"J_{vid}", elev + 1.0, jx + 60, jy - 80)
            pipe(f"p_{vid}", j, f"J_{vid}", 20, 250)
            valves.append(f" {vid:<10} J_{vid:<8} {sid:<10} 250 PRV 32 0")

    # assemble ---------------------------------------------------------------
    out = ["[TITLE]",
           "SCADAVT small-town water distribution system (six areas, two works)",
           "",
           "[JUNCTIONS]", ";ID         Elev     Demand", *junctions, "",
           "[RESERVOIRS]", ";ID         Head", *reservoirs, "",
           "[TANKS]", ";ID Elev InitLvl MinLvl MaxLvl Diam MinVol VolCurve"]
    for tid, (e, i, lo, hi, d) in TANKS.items():
        out.append(f" {tid:<10} {e:6.1f} {i:6.2f} {lo:6.2f} {hi:6.2f} {d:6.1f} 0")
    out += ["", "[PIPES]", ";ID Node1 Node2 Length Diam Rough MinorLoss Status",
            *pipes, "",
            "[PUMPS]", ";ID Node1 Node2 Parameters", *pumps, "",
            "[VALVES]", ";ID Node1 Node2 Diam Type Setting MinorLoss",
            *valves, "",
            "[CURVES]", ";ID Flow Head", *_pump_curve_lines(), "",
            "[STATUS]",
            # pumps start off; the control system switches them on
            *[f" {p[0]:<10} Closed" for p in PUMPS], "",
            "[ENERGY]", " Global Efficiency 75", " Global Price 0", "",
            "[TIMES]", " Duration 720:00", " Hydraulic Timestep 0:05",
            " Pattern Timestep 1:00", " Report Timestep 1:00",
            " Start ClockTime 12 am", "",
            "[OPTIONS]", " Units LPM", " Headloss H-W", " Trials 200",
            " Accuracy 0.001", " Demand Model PDA",
            " Minimum Pressure 0", " Required Pressure 15",
            " Pressure Exponent 0.5", "",
            "[REPORT]", " Status No", " Summary No", "",
            "[COORDINATES]", ";Node X Y"]
    for nid, (x, y) in coords.items():
        out.append(f" {nid:<10} {x:10.1f} {y:10.1f}")
    out += ["", "[END]", ""]
    path.write_text("\n".join(out))
    return path


if __name__ == "__main__":
    print(build())
