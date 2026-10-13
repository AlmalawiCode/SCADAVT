"""Vector icons for SCADA diagrams (matplotlib patches, no bitmaps).

Each function draws one icon centred at (x, y) with overall size ``s`` in data
units on an axes that uses an equal aspect ratio, and returns nothing. The
icons use the strong house palette with black outlines; they never draw a
coloured background, so they sit on the white page of a journal figure.
"""

from __future__ import annotations

import numpy as np
from matplotlib.patches import (Circle, Ellipse, FancyBboxPatch, Polygon,
                                Rectangle, Wedge)

BLUE, RED, GREEN, ORANGE, PURPLE = "#3498DB", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6"
DARK, STEEL, LIGHT, WATER = "#2C3E50", "#7F8C8D", "#ECF0F1", "#5DADE2"
LW = 0.9


def _box(ax, x, y, w, h, fc, ec=DARK, r=0.08, lw=LW, z=3):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                                fc=fc, ec=ec, lw=lw, zorder=z))


def plc(ax, x, y, s=1.0, label=None, fs=6, hot=False):
    """A DIN-rail PLC: CPU module with LEDs and two I/O modules."""
    w, h = 1.0 * s, 0.72 * s
    x0, y0 = x - w / 2, y - h / 2
    ax.add_patch(Rectangle((x0 - 0.05 * s, y - 0.06 * s), w + 0.1 * s, 0.12 * s,
                           fc=STEEL, ec=DARK, lw=LW * 0.6, zorder=2))  # rail
    _box(ax, x0, y0, 0.44 * w, h, RED if hot else DARK, r=0.04 * s)
    for i, c in enumerate((GREEN, ORANGE, GREEN)):
        ax.add_patch(Circle((x0 + 0.1 * w, y0 + h * (0.78 - 0.16 * i)), 0.035 * s,
                            fc=c, ec="none", zorder=4))
    ax.add_patch(Rectangle((x0 + 0.2 * w, y0 + 0.14 * h), 0.18 * w, 0.3 * h,
                           fc=LIGHT, ec="none", zorder=4))  # port
    for k, c in enumerate((BLUE, GREEN)):
        mx = x0 + (0.47 + 0.27 * k) * w
        _box(ax, mx, y0, 0.25 * w, h, c, r=0.03 * s)
        for j in range(4):
            ax.add_patch(Rectangle((mx + 0.05 * w, y0 + h * (0.12 + 0.2 * j)),
                                   0.15 * w, 0.07 * h, fc="white", ec="none", zorder=4))
    if label:
        ax.text(x, y0 - 0.08 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def pump(ax, x, y, s=1.0, on=True, label=None, fs=6):
    """Centrifugal pump driven by a motor."""
    c = GREEN if on else STEEL
    ax.add_patch(Rectangle((x - 0.55 * s, y - 0.12 * s), 0.38 * s, 0.24 * s,
                           fc=STEEL, ec=DARK, lw=LW, zorder=3))  # motor body
    for k in range(3):
        ax.plot([x - 0.5 * s + 0.1 * s * k] * 2, [y - 0.12 * s, y + 0.12 * s],
                color=DARK, lw=0.4, zorder=4)  # cooling fins
    ax.add_patch(Circle((x + 0.05 * s, y), 0.26 * s, fc=c, ec=DARK, lw=LW, zorder=3))
    ax.add_patch(Rectangle((x + 0.05 * s, y + 0.14 * s), 0.38 * s, 0.12 * s,
                           fc=c, ec=DARK, lw=LW, zorder=2))  # outlet
    for a in range(0, 360, 90):
        ax.add_patch(Wedge((x + 0.05 * s, y), 0.18 * s, a, a + 40, fc="white",
                           ec="none", zorder=4))  # impeller
    ax.add_patch(Circle((x + 0.05 * s, y), 0.05 * s, fc=DARK, ec="none", zorder=5))
    if label:
        ax.text(x, y - 0.34 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def tank(ax, x, y, s=1.0, level=0.6, label=None, fs=6, critical=0.15):
    """Elevated storage tank with its water level and the critical mark."""
    w, h = 0.8 * s, 0.75 * s
    x0, y0 = x - w / 2, y - h / 2 + 0.15 * s
    for dx in (-0.3, 0.3):
        ax.plot([x + dx * s, x + dx * 1.3 * s], [y0, y - 0.6 * s], color=DARK,
                lw=LW * 1.3, zorder=2)  # legs
    ax.add_patch(Rectangle((x0, y0), w, h, fc=LIGHT, ec=DARK, lw=LW, zorder=3))
    ax.add_patch(Rectangle((x0, y0), w, h * level, fc=WATER, ec="none", zorder=4))
    ax.plot([x0, x0 + w], [y0 + h * critical] * 2, color=RED, lw=0.8, ls="--", zorder=5)
    ax.add_patch(Ellipse((x, y0 + h), w, 0.18 * s, fc=STEEL, ec=DARK, lw=LW, zorder=5))
    if label:
        ax.text(x, y0 + h + 0.13 * s, label, ha="center", va="bottom", fontsize=fs,
                fontweight="bold", color=DARK, zorder=6)


def valve(ax, x, y, s=1.0, open_=True, label=None, fs=6, vertical=False, color=None):
    """Gate valve (bow-tie) with an actuator stem."""
    c = color or (BLUE if open_ else RED)
    d = 0.22 * s
    if vertical:
        tri1 = [(x - d, y + d), (x + d, y + d), (x, y)]
        tri2 = [(x - d, y - d), (x + d, y - d), (x, y)]
    else:
        tri1 = [(x - d, y - d), (x - d, y + d), (x, y)]
        tri2 = [(x + d, y - d), (x + d, y + d), (x, y)]
    ax.add_patch(Polygon(tri1, fc=c, ec=DARK, lw=LW, zorder=4))
    ax.add_patch(Polygon(tri2, fc=c, ec=DARK, lw=LW, zorder=4))
    if not vertical:
        ax.plot([x, x], [y, y + 0.32 * s], color=DARK, lw=LW, zorder=4)
        ax.add_patch(Rectangle((x - 0.1 * s, y + 0.3 * s), 0.2 * s, 0.1 * s,
                               fc=DARK, ec=DARK, zorder=4))
    if label:
        ax.text(x, y - 0.28 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def router(ax, x, y, s=1.0, label=None, fs=6, color=BLUE):
    """Network router: a short cylinder with four arrows."""
    w, h = 0.8 * s, 0.25 * s
    ax.add_patch(Rectangle((x - w / 2, y - h / 2), w, h, fc=color, ec="none", zorder=3))
    ax.add_patch(Ellipse((x, y - h / 2), w, 0.3 * s, fc=color, ec=DARK, lw=LW, zorder=2))
    ax.plot([x - w / 2] * 2, [y - h / 2, y + h / 2], color=DARK, lw=LW, zorder=4)
    ax.plot([x + w / 2] * 2, [y - h / 2, y + h / 2], color=DARK, lw=LW, zorder=4)
    ax.add_patch(Ellipse((x, y + h / 2), w, 0.3 * s, fc=color, ec=DARK, lw=LW, zorder=4))
    for dx, dy in ((0.14, 0.04), (-0.14, -0.04)):
        ax.annotate("", xy=(x + dx * s * 1.5, y + h / 2 + dy * s),
                    xytext=(x - dx * s * 0.2, y + h / 2 + dy * s),
                    arrowprops=dict(arrowstyle="-|>", color="white", lw=0.9,
                                    mutation_scale=4), zorder=5)
    if label:
        ax.text(x, y - 0.3 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def server(ax, x, y, s=1.0, label=None, fs=6, color=PURPLE):
    """Rack server (MTU, historian, gateway)."""
    w, h = 0.6 * s, 0.9 * s
    _box(ax, x - w / 2, y - h / 2, w, h, DARK, r=0.04 * s)
    for i in range(3):
        yy = y - h / 2 + h * (0.12 + 0.29 * i)
        _box(ax, x - w / 2 + 0.06 * s, yy, w - 0.12 * s, 0.2 * h, color, r=0.02 * s, z=4)
        ax.add_patch(Circle((x + w / 2 - 0.13 * s, yy + 0.1 * h), 0.03 * s, fc=GREEN,
                            ec="none", zorder=5))
    if label:
        ax.text(x, y - h / 2 - 0.06 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def hmi(ax, x, y, s=1.0, label=None, fs=6):
    """Operator workstation showing a small trend."""
    w, h = 1.0 * s, 0.66 * s
    _box(ax, x - w / 2, y - h / 2 + 0.12 * s, w, h, DARK, r=0.04 * s)
    ax.add_patch(Rectangle((x - w / 2 + 0.06 * s, y - h / 2 + 0.18 * s), w - 0.12 * s,
                           h - 0.12 * s, fc="#FDFEFE", ec="none", zorder=4))
    xs = np.linspace(x - 0.38 * s, x + 0.38 * s, 9)
    ys = y + 0.08 * s + 0.12 * s * np.sin(np.linspace(0, 3 * np.pi, 9))
    ax.plot(xs, ys, color=BLUE, lw=1.0, zorder=5)
    ax.add_patch(Rectangle((x - 0.36 * s, y - 0.12 * s), 0.18 * s, 0.1 * s, fc=GREEN, zorder=5))
    ax.add_patch(Rectangle((x - 0.14 * s, y - 0.12 * s), 0.18 * s, 0.1 * s, fc=RED, zorder=5))
    ax.add_patch(Polygon([(x - 0.12 * s, y - h / 2 + 0.12 * s), (x + 0.12 * s, y - h / 2 + 0.12 * s),
                          (x + 0.2 * s, y - h / 2 - 0.02 * s), (x - 0.2 * s, y - h / 2 - 0.02 * s)],
                         fc=STEEL, ec=DARK, lw=LW, zorder=3))
    if label:
        ax.text(x, y - h / 2 - 0.08 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def cloud(ax, x, y, s=1.0, label=None, fs=7, color="#D6EAF8"):
    """Wide-area network cloud."""
    for dx, dy, r in ((-0.55, 0, 0.42), (0, 0.18, 0.55), (0.55, 0, 0.42),
                      (-0.2, -0.12, 0.42), (0.25, -0.12, 0.42)):
        ax.add_patch(Circle((x + dx * s, y + dy * s), r * s, fc=color, ec=DARK,
                            lw=LW, zorder=2))
    for dx, dy, r in ((-0.55, 0, 0.42), (0, 0.18, 0.55), (0.55, 0, 0.42),
                      (-0.2, -0.12, 0.42), (0.25, -0.12, 0.42)):
        ax.add_patch(Circle((x + dx * s, y + dy * s), r * s - 0.02 * s, fc=color,
                            ec="none", zorder=3))
    if label:
        ax.text(x, y, label, ha="center", va="center", fontsize=fs, fontweight="bold",
                color=DARK, zorder=4)


def house(ax, x, y, s=1.0, color=ORANGE, label=None, fs=6):
    """A group of houses (a sub-area)."""
    ax.add_patch(Rectangle((x - 0.25 * s, y - 0.25 * s), 0.5 * s, 0.32 * s, fc="#FDEBD0",
                           ec=DARK, lw=LW, zorder=3))
    ax.add_patch(Polygon([(x - 0.32 * s, y + 0.07 * s), (x + 0.32 * s, y + 0.07 * s),
                          (x, y + 0.32 * s)], fc=color, ec=DARK, lw=LW, zorder=3))
    ax.add_patch(Rectangle((x - 0.06 * s, y - 0.25 * s), 0.12 * s, 0.17 * s, fc=DARK, zorder=4))
    if label:
        ax.text(x, y - 0.3 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def works(ax, x, y, s=1.0, label=None, fs=6):
    """Water treatment works: two basins and a building."""
    ax.add_patch(Rectangle((x - 0.5 * s, y - 0.25 * s), 1.0 * s, 0.5 * s, fc=LIGHT,
                           ec=DARK, lw=LW, zorder=3))
    for dx in (-0.25, 0.25):
        ax.add_patch(Circle((x + dx * s, y), 0.17 * s, fc=WATER, ec=DARK, lw=LW, zorder=4))
    if label:
        ax.text(x, y - 0.32 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=DARK, zorder=5)


def attacker(ax, x, y, s=1.0, label=None, fs=6):
    """Hooded figure used to mark an adversary position."""
    ax.add_patch(Circle((x, y + 0.18 * s), 0.18 * s, fc=DARK, ec=DARK, zorder=4))
    ax.add_patch(Wedge((x, y - 0.25 * s), 0.35 * s, 0, 180, fc=DARK, ec=DARK, zorder=3))
    ax.add_patch(Rectangle((x - 0.1 * s, y + 0.15 * s), 0.2 * s, 0.05 * s, fc=RED, zorder=5))
    if label:
        ax.text(x, y - 0.32 * s, label, ha="center", va="top", fontsize=fs,
                fontweight="bold", color=RED, zorder=5)
