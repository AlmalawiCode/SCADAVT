"""Shared matplotlib style: Times-like serif, light dashed grid, strong house palette, white background."""

import matplotlib as mpl

PALETTE = ["#3498DB", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6"]


def apply_style():
    mpl.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Nimbus Roman", "Nimbus Roman No9 L", "STIXGeneral",
                       "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 8,
        "axes.titlesize": 8.5,
        "axes.labelsize": 8,
        "legend.fontsize": 7,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "axes.grid": True,
        "grid.linestyle": "--",
        "grid.linewidth": 0.5,
        "grid.alpha": 0.5,
        "axes.edgecolor": "#2C3E50",
        "axes.linewidth": 0.8,
        "legend.frameon": True,
        "legend.edgecolor": "#2C3E50",
        "legend.fancybox": False,
        "savefig.dpi": 300,
        "pdf.fonttype": 42,
        "axes.prop_cycle": mpl.cycler(color=PALETTE),
    })
