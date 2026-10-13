"""Explanatory diagrams of the paper (architecture, water network, SCADA topology,
protocol data units). All drawn as vector graphics with the icons module.

    python -m scadavt.analysis.diagrams --out ../paper/figures
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

from .. import scenario as sc
from . import icons as I
from .style import apply_style

C = {"blue": I.BLUE, "red": I.RED, "green": I.GREEN, "orange": I.ORANGE, "purple": I.PURPLE}


def frame(ax, x, y, w, h, title, color=I.DARK, dashed=True, fs=7.5, fill=None):
    """Dashed coloured frame with a coloured title; the inside stays white."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.15",
                                fc="none", ec=color, lw=1.4,
                                ls=(0, (5, 3)) if dashed else "-", zorder=1))
    ax.text(x + 0.15, y + h - 0.12, title, ha="left", va="top", fontsize=fs,
            fontweight="bold", color=color, zorder=6)


def arrow(ax, a, b, color=I.DARK, both=False, lw=1.1, ls="-", rad=0.0, text=None, fs=6,
          toff=(0, 0.12)):
    st = "<|-|>" if both else "-|>"
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle=st, mutation_scale=8, color=color, lw=lw,
                                 ls=ls, connectionstyle=f"arc3,rad={rad}", zorder=2))
    if text:
        ax.text((a[0] + b[0]) / 2 + toff[0], (a[1] + b[1]) / 2 + toff[1], text, ha="center",
                va="bottom", fontsize=fs, color=color, fontweight="bold", zorder=6,
                bbox=dict(fc="white", ec="none", pad=0.6, alpha=0.9))


def box(ax, x, y, w, h, text, fc=I.BLUE, fs=6.5, tc="white"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.08",
                                fc=fc, ec=I.DARK, lw=0.9, zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, fontweight="bold", zorder=4)


# ---------------------------------------------------------------------------
def architecture(out: Path):
    fig, ax = plt.subplots(figsize=(7.2, 4.1))
    ax.set_xlim(0, 20)
    ax.set_ylim(0, 11.2)
    ax.set_aspect("equal")
    ax.axis("off")
    # internal world --------------------------------------------------------
    frame(ax, 0.2, 5.1, 12.9, 5.9, "Internal world: emulated SCADA network", I.GREEN,
          fill="#F4FBF6")
    I.server(ax, 1.9, 7.9, s=1.0)
    ax.text(1.9, 8.5, "MTU +\nHMI server", ha="center", va="bottom", fontsize=6.2,
            fontweight="bold", color=I.DARK)
    I.cloud(ax, 5.7, 7.9, s=1.05, label="Wide-area\nnetwork", fs=6.5, color="white")
    arrow(ax, (2.45, 7.9), (4.3, 7.9), both=True, lw=0.9)
    for k, y in enumerate((9.6, 7.9, 6.2)):
        I.router(ax, 8.9, y, s=0.6)
        I.plc(ax, 10.4, y, s=0.62)
        I.plc(ax, 11.85, y, s=0.62)
        arrow(ax, (7.1, 7.9 + (y - 7.9) * 0.35), (8.45, y), both=True, lw=0.8)
        ax.plot([9.25, 9.95], [y, y], color=I.DARK, lw=0.8, zorder=1)
    ax.text(11.1, 10.35, "PLC = Modbus/TCP slave\n+ I/O modules simulator", ha="center",
            fontsize=5.8, color="black", fontweight="bold")
    # controlled environment ------------------------------------------------
    frame(ax, 13.5, 0.3, 6.3, 10.7, "External world:\ncontrolled environment", I.BLUE,
          fill="#F3F8FC")
    I.works(ax, 14.6, 8.3, s=0.8)
    ax.plot([15.0, 18.6], [8.3, 8.3], color=I.DARK, lw=1.2, zorder=1)
    I.pump(ax, 16.3, 8.3, s=0.7)
    I.tank(ax, 18.6, 8.85, s=0.85, level=0.55)
    for xx in (14.5, 15.7, 16.9, 18.1):
        I.house(ax, xx, 6.6, s=0.7)
    ax.text(16.65, 5.25, "EPANET hydraulic engine\n+ residential demand model", ha="center",
            va="center", fontsize=6.3, fontweight="bold", color="black")
    box(ax, 14.0, 2.6, 5.3, 1.1, "WaterSystem server", I.BLUE, fs=6.6)
    ax.text(16.65, 1.6, "simulated plant clock,\nground-truth log", ha="center", va="center",
            fontsize=5.8, style="italic", color=I.DARK)
    # bridge ----------------------------------------------------------------
    frame(ax, 4.6, 0.3, 8.5, 4.4, "Bridge between the two worlds", I.ORANGE, fill="#FFF9F0")
    box(ax, 5.1, 2.6, 3.4, 1.1, "IOModuleGate", I.ORANGE, fs=6.6)
    box(ax, 9.2, 2.6, 3.4, 1.1, "Meta-converter", I.ORANGE, fs=6.6)
    arrow(ax, (8.5, 3.15), (9.2, 3.15), both=True)
    ax.text(8.85, 1.25, "tag mapping, exchange period,\nIOModules protocol on the back channel",
            ha="center", va="center", fontsize=5.8, style="italic", color=I.DARK)
    arrow(ax, (10.4, 5.5), (6.8, 3.7), both=True, ls="--")
    ax.text(9.75, 4.2, "back channel", ha="left", va="center", fontsize=6,
            fontweight="bold", color=I.DARK)
    arrow(ax, (12.6, 3.15), (14.0, 3.15), both=True, ls="--")
    ax.text(13.3, 3.45, "WDS\nprotocol", ha="center", va="bottom", fontsize=5.6,
            fontweight="bold", color=I.DARK)
    # operator --------------------------------------------------------------
    frame(ax, 0.2, 0.3, 4.0, 4.4, "Operator", I.PURPLE, fill="#F8F3FA")
    I.hmi(ax, 1.3, 2.5, s=0.95)
    ax.text(1.3, 1.25, "HMI client", ha="center", fontsize=6, fontweight="bold", color=I.DARK)
    I.server(ax, 3.2, 2.45, s=0.75, color=I.BLUE)
    ax.text(3.2, 1.25, "Historian", ha="center", fontsize=6, fontweight="bold", color=I.DARK)
    arrow(ax, (1.9, 3.3), (1.9, 7.4), both=True, ls="--", text="Modbus/TCP", toff=(0.0, 0.9))
    fig.savefig(out / "fig_architecture.pdf", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
def water_network(out: Path):
    """Schematic of the small-town water distribution system."""
    fig, ax = plt.subplots(figsize=(7.2, 3.9))
    ax.set_xlim(0, 20.2)
    ax.set_ylim(-0.1, 10.6)
    ax.set_aspect("equal")
    ax.axis("off")

    def line(*pts, **kw):
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color="#34495E", lw=1.5, zorder=1, solid_capstyle="round", **kw)

    def area(x0, x1, y0, name, subs, color):
        frame(ax, x0, y0, x1 - x0, 2.0, f"Area {name}", color, fs=6.3)
        n = len(subs)
        xs = [x0 + (x1 - x0) * (i + 0.5) / n for i in range(n)]
        line((xs[0], y0 + 1.45), (xs[-1], y0 + 1.45))
        for x, sid in zip(xs, subs):
            line((x, y0 + 1.45), (x, y0 + 1.0))
            I.valve(ax, x, y0 + 1.2, s=0.38, vertical=True, color=I.BLUE)
            I.house(ax, x, y0 + 0.62, s=0.62, color=color)
            ax.text(x + 0.33, y0 + 0.45, sid, fontsize=5.4, fontweight="bold", color=I.DARK,
                    va="center")
        return xs

    def tank(x, y, name, level):
        I.tank(ax, x, y, s=0.95, level=level)
        ax.text(x + 0.55, y + 0.4, name, fontsize=7, fontweight="bold", color=I.DARK)

    def pump(x, y, name, vertical_label=False):
        I.pump(ax, x, y, s=0.6)
        ax.text(x, y - 0.32, name, ha="center", va="top", fontsize=5.6, fontweight="bold",
                color=I.DARK)

    # system 1 --------------------------------------------------------------
    I.works(ax, 0.9, 1.2, s=0.9)
    ax.text(0.9, 0.6, "Works 1", ha="center", va="top", fontsize=6, fontweight="bold")
    line((1.35, 1.2), (3.6, 1.2), (3.6, 1.95))
    pump(2.4, 1.2, "P1-P3")
    tank(3.6, 2.4, "T1", 0.55)
    line((3.6, 1.7), (3.6, 1.2))
    line((4.0, 1.95), (4.9, 1.95), (4.9, 1.45), (6.6, 1.45))
    area(5.6, 10.0, 0.25, "A", ["A1", "A2", "A3", "A4"], I.ORANGE)
    line((3.6, 3.15), (3.6, 5.7))
    pump(3.6, 4.3, "P4, P5")
    tank(3.6, 6.2, "T2", 0.4)
    line((3.2, 5.95), (2.6, 5.95), (2.6, 7.2))
    area(0.15, 3.0, 7.0, "B", ["B1", "B2", "B3"], I.GREEN)
    line((4.0, 5.95), (8.0, 5.95), (8.0, 5.95))
    I.valve(ax, 5.9, 5.95, s=0.7, color=I.RED)
    ax.text(5.9, 5.35, "V1", ha="center", va="top", fontsize=6.5, fontweight="bold",
            color=I.RED)
    tank(8.0, 6.45, "T3", 0.7)
    line((8.0, 7.2), (8.0, 8.25))
    area(5.6, 10.0, 8.25, "C", ["C1", "C2", "C3", "C4"], I.PURPLE)
    # system 2 --------------------------------------------------------------
    I.works(ax, 19.3, 1.2, s=0.9)
    ax.text(19.3, 0.6, "Works 2", ha="center", va="top", fontsize=6, fontweight="bold")
    line((18.85, 1.2), (16.4, 1.2), (16.4, 1.95))
    pump(17.6, 1.2, "P8, P9")
    tank(16.4, 2.4, "T4", 0.6)
    line((16.0, 1.95), (15.2, 1.95), (15.2, 1.45), (14.0, 1.45))
    area(10.6, 14.6, 0.25, "D", ["D1", "D2", "D3"], I.BLUE)
    line((16.4, 3.15), (16.4, 5.7))
    pump(16.4, 4.3, "P10, P11")
    tank(16.4, 6.2, "T5", 0.5)
    line((16.8, 5.95), (17.6, 5.95), (17.6, 7.2))
    area(16.9, 20.1, 7.0, "E", ["E1", "E2", "E3"], I.ORANGE)
    line((19.3, 1.65), (19.3, 3.6), (12.6, 3.6), (12.6, 5.7))
    pump(14.1, 3.6, "P6, P7")
    tank(12.6, 6.2, "T6", 0.55)
    line((12.6, 6.95), (12.6, 8.25))
    area(10.6, 14.6, 8.25, "F", ["F1", "F2", "F3"], I.GREEN)
    line((12.2, 5.95), (8.4, 5.95))
    pump(10.3, 5.95, "P12")
    ax.text(10.1, 4.85, "support of area C\nfrom works 2", ha="center", fontsize=5.4,
            style="italic", color=I.DARK)
    fig.savefig(out / "fig_water_network.pdf", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
def scada_topology(out: Path):
    scen = sc.load()
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    ax.set_xlim(-13.2, 10.5)
    ax.set_ylim(-6.6, 6.4)
    ax.set_aspect("equal")
    ax.axis("off")
    I.cloud(ax, 0, 0.3, s=1.9, label="Wide-area\nnetwork", fs=7, color="white")
    import numpy as np
    fields = list(scen.fields.items())
    n = len(fields)
    slots = [np.pi / 2 - 2 * np.pi * (i + 0.5) / (n + 1) for i in range(n + 1)]
    ctrl = min(range(n + 1), key=lambda i: abs(np.cos(slots[i]) + 1))  # the left-most slot
    site_slots = [a for i, a in enumerate(slots) if i != ctrl]
    for i, (f, plcs) in enumerate(fields):
        ang = site_slots[i]
        rx, ry = 4.6 * np.cos(ang), 3.3 * np.sin(ang)
        px, py = 8.2 * np.cos(ang), 5.4 * np.sin(ang)
        ax.plot([0.9 * np.cos(ang) * 1.6, rx], [0.3 + 0.9 * np.sin(ang), ry], color=I.STEEL,
                lw=0.8, zorder=1)
        ax.plot([rx, px], [ry, py], color=I.STEEL, lw=0.6, ls=":", zorder=1)
        I.router(ax, rx, ry, s=0.55)
        ax.text(rx, ry + 0.28, f"Site {f}", ha="center", va="bottom", fontsize=5.5,
                fontweight="bold", color=I.DARK, zorder=6)
        k = len(plcs)
        for j, p in enumerate(plcs):
            off = (j - (k - 1) / 2) * 0.95
            tx, ty = px - np.sin(ang) * off, py + np.cos(ang) * off * 0.75
            hot = p in ("PLC1", "PLC4", "PLC6", "PLC7")
            I.plc(ax, tx, ty, s=0.48, hot=hot)
            ax.text(tx, ty - 0.25, p.replace("PLC", "P"), ha="center", va="top", fontsize=4.8,
                    fontweight="bold", color=I.RED if hot else I.DARK, zorder=6)
    # control centre in the last slot
    ang = slots[ctrl]
    rx, ry = 4.6 * np.cos(ang), 3.3 * np.sin(ang)
    ax.plot([-2.2, rx], [0.3, ry], color=I.DARK, lw=1.2, zorder=1)
    I.router(ax, rx, ry, s=0.55, color=I.PURPLE)
    ax.plot([rx - 0.4, -11.4], [ry, ry], color=I.DARK, lw=1.0, zorder=1)
    I.server(ax, -11.4, ry + 0.75, s=0.75)
    I.hmi(ax, -11.4, ry - 0.95, s=0.8)
    ax.text(-11.4, ry + 1.45, "Control centre", ha="center", fontsize=6.2,
            fontweight="bold", color=I.PURPLE)
    ax.text(-11.4, ry - 1.65, "MTU, HMI server,\nhistorian, HMI client", ha="center", va="top",
            fontsize=5.6, color=I.DARK)
    ax.text(-13.0, -6.4, "Red PLCs supervise the T1-T2-T3 subsystem used in the fault experiments "
            "(PLC1: T2 level, PLC4: pumps P4/P5, PLC6: valve V1, PLC7: T3 level).",
            fontsize=5.8, color=I.DARK, style="italic")
    fig.savefig(out / "fig_scada_topology.pdf", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
def protocols(out: Path):
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 2.1))
    specs = [
        ("IOModules protocol (19 bytes)", [("TransactionNo", 4, I.BLUE), ("Function", 1, I.ORANGE),
                                           ("ProcessID", 10, I.GREEN), ("Data (float32)", 4, I.PURPLE)]),
        ("WaterSystem (WDS) protocol (17 bytes)", [("ProcessID", 10, I.GREEN), ("Function", 1, I.ORANGE),
                                                   ("Param. type", 1, I.RED), ("Data type", 1, I.BLUE),
                                                   ("Data (float32)", 4, I.PURPLE)]),
    ]
    for ax, (title, fields) in zip(axes, specs):
        ax.axis("off")
        total = sum(f[1] for f in fields)
        x = 0.0
        for name, n, col in fields:
            w = max(n, 2.2) / (sum(max(f[1], 2.2) for f in fields))
            ax.add_patch(Rectangle((x, 0.15), w, 0.55, fc=col, ec=I.DARK, lw=0.9))
            ax.text(x + w / 2, 0.42, name, ha="center", va="center", fontsize=6.5,
                    color="white", fontweight="bold")
            ax.text(x + w / 2, 0.82, f"{n} byte" + ("s" if n > 1 else ""), ha="center",
                    va="center", fontsize=6, color=I.DARK)
            x += w
        ax.set_title(title, fontsize=7.5, fontweight="bold", loc="left", pad=1)
        ax.set_xlim(-0.01, 1.01)
        ax.set_ylim(0, 1.0)
    fig.tight_layout(h_pad=0.4)
    fig.savefig(out / "fig_protocols.pdf", bbox_inches="tight")
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("figures"))
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    apply_style()
    architecture(a.out)
    water_network(a.out)
    scada_topology(a.out)
    protocols(a.out)
    print("diagrams written to", a.out)


if __name__ == "__main__":
    main()
