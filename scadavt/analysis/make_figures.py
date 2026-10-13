"""Draw every result figure and table of the paper from the result files.

    python -m scadavt.analysis.make_figures --results results --out ../paper

No number is typed by hand: each value comes from results/summary.csv or the
time-series files written by the experiment suite.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..experiments.suite import CRITICAL, SEASON_STARTS, START, WINDOWS
from ..wds.demand import DEFAULT_PROFILE
from .style import PALETTE, apply_style

BLUE, RED, GREEN, ORANGE, PURPLE = PALETTE
BLACK, GREY = "#2C3E50", "#7F8C8D"
DATA = Path(__file__).resolve().parents[2] / "data"


def legend_top(ax, ncol, **kw):
    return ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=ncol, frameon=True,
                     edgecolor="#2C3E50", fancybox=False, borderaxespad=0.3, **kw)


def fig_demand(out):
    s = pd.read_csv(DATA / "consumption_per_person.csv", parse_dates=["date"])
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.2, 2.3), gridspec_kw={"width_ratios": [2.2, 1]})
    a.plot(s["date"], s["litres_per_person_per_day"], color=BLUE, lw=1.3,
           label="Weekly mean use per person")
    for name, d in SEASON_STARTS.items():
        a.axvline(pd.Timestamp(d), color=RED if d == START else GREY, lw=1.2,
                  ls="-" if d == START else ":")
        a.text(pd.Timestamp(d) + pd.Timedelta(days=12), 124, name, rotation=90, fontsize=5.5, ha="left", va="bottom",
               color=RED if d == START else BLACK, fontweight="bold" if d == START else "normal")
    a.set_ylabel("Litres per person per day")
    a.set_ylim(120, 260)
    a.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    a.set_title("(a) Melbourne weekly water use, 2022-2026", fontweight="bold")
    hours = np.arange(24)
    b.bar(hours, np.array(DEFAULT_PROFILE) * 24, color=ORANGE, edgecolor="black", lw=0.5)
    b.axhline(1.0, color="#2C3E50", lw=0.8, ls="--")
    b.set_xlabel("Hour of day")
    b.set_ylabel("Hourly use / mean hour")
    b.set_xticks([0, 6, 12, 18, 23])
    b.set_title("(b) Diurnal profile", fontweight="bold")
    fig.tight_layout()
    fig.savefig(out / "fig_demand.pdf", bbox_inches="tight")
    plt.close(fig)


def _levels(ax, df, title, windows=()):
    for x0, x1 in windows:
        ax.axvspan(x0, x1, fc="none", ec=RED, hatch="////", lw=0.0, alpha=0.45)
        ax.axvline(x0, color=RED, lw=0.8)
        ax.axvline(x1, color=RED, lw=0.8)
    ax.plot(df["sim_h"], df["T2_level"], color=BLUE, lw=1.3, label="Tank T2")
    ax.plot(df["sim_h"], df["T3_level"], color=PURPLE, lw=1.3, label="Tank T3")
    ax.axhline(CRITICAL, color=RED, lw=1.1, ls="--", label="Critical level")
    ax.set_ylim(0, 7.4)
    ax.set_xlim(0, 168)
    ax.set_xticks(range(0, 169, 24))
    ax.set_ylabel("Water level (m)")
    ax.set_title(title, fontweight="bold", loc="left", fontsize=8)


def fig_e1_timeseries(res, out):
    off = pd.read_csv(res / "ts_E1_noctrl.csv")
    on = pd.read_csv(res / "ts_E1_ctrl.csv")
    fig, (a, b) = plt.subplots(2, 1, figsize=(7.2, 3.6), sharex=True)
    _levels(a, off, "(a) Valve V1 at a fixed setting (no supervision)")
    _levels(b, on, "(b) Valve V1 adjusted by the MTU (flow balancing)")
    b.set_xlabel("Simulated time (hours)")
    a.legend(loc="lower center", bbox_to_anchor=(0.5, 1.12), ncol=3, frameon=True,
             edgecolor="#2C3E50", fancybox=False)
    fig.tight_layout(h_pad=0.6)
    fig.savefig(out / "fig_e1_levels.pdf", bbox_inches="tight")
    plt.close(fig)


def _bars(ax, groups, labels, colors, ylabel, title):
    x = np.arange(len(groups))
    for i, (vals, c) in enumerate(zip(groups, colors)):
        m = float(np.mean(vals))
        ax.bar(i, m, width=0.6, color=c, edgecolor="black", lw=0.7, zorder=2)
        jitter = np.linspace(-0.15, 0.15, len(vals))
        ax.scatter(i + jitter, vals, s=9, color="white", edgecolor="black", lw=0.6, zorder=3)
        ax.text(i, m, f"{m:.1f}", ha="center", va="bottom", fontsize=7, fontweight="bold",
                zorder=4, bbox=dict(fc="white", ec="none", pad=0.5, alpha=0.8))
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontweight="bold", fontsize=8)


def fig_e1_bars(res, out):
    d = pd.read_csv(res / "summary.csv")
    e = d[d.exp == "E1"]
    off, on = e[~e.control], e[e.control]
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.3))
    _bars(axes[0], [off.T2_hours_below, on.T2_hours_below], ["Fixed V1", "Supervised"],
          [RED, BLUE], "Hours below critical", "(a) Tank T2")
    _bars(axes[1], [off.T3_hours_below, on.T3_hours_below], ["Fixed V1", "Supervised"],
          [RED, BLUE], "Hours below critical", "(b) Tank T3")
    _bars(axes[2], [off.B_unmet_m3 + off.C_unmet_m3, on.B_unmet_m3 + on.C_unmet_m3],
          ["Fixed V1", "Supervised"], [RED, BLUE], "Unmet demand (m$^3$)", "(c) Areas B and C")
    fig.tight_layout(w_pad=1.0)
    fig.savefig(out / "fig_e1_bars.pdf", bbox_inches="tight")
    plt.close(fig)


def fig_e3_timeseries(res, out):
    hold = pd.read_csv(res / "ts_E3_hold_PLC6.csv")
    off = pd.read_csv(res / "ts_E3_off_PLC4.csv")
    fig, (a, b) = plt.subplots(2, 1, figsize=(7.2, 3.6), sharex=True)
    _levels(a, hold, "(a) Loss of the control channel to PLC6 (valve V1 keeps its last setting)",
            WINDOWS)
    _levels(b, off, "(b) Pumps P4 and P5 held off at PLC4", WINDOWS)
    b.set_xlabel("Simulated time (hours)")
    h, l = a.get_legend_handles_labels()
    from matplotlib.patches import Patch
    h.append(Patch(fc="none", ec=RED, hatch="////"))
    l.append("Fault window")
    a.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 1.12), ncol=4, frameon=True,
             edgecolor="#2C3E50", fancybox=False)
    fig.tight_layout(h_pad=0.6)
    fig.savefig(out / "fig_e3_levels.pdf", bbox_inches="tight")
    plt.close(fig)


def fig_e4_sweep(res, out):
    d = pd.read_csv(res / "summary.csv")
    e = d[d.exp == "E4"].copy()
    e["unmet"] = e.B_unmet_m3 + e.C_unmet_m3
    e["below"] = e.T2_hours_below + e.T3_hours_below
    base = d[(d.exp == "E1") & d.control & (d.seed <= 5)]
    base_unmet = float((base.B_unmet_m3 + base.C_unmet_m3).mean())
    base_below = float((base.T2_hours_below + base.T3_hours_below).mean())
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.5))
    names = {"hold:PLC6": ("Valve V1 not updated (PLC6)", BLUE, "o"),
             "off:PLC4": ("Pumps P4/P5 held off (PLC4)", RED, "s")}
    for ax, col, ylab, b in ((axes[0], "unmet", "Unmet demand, areas B+C (m$^3$)", base_unmet),
                             (axes[1], "below", "Hours below critical, T2+T3", base_below)):
        for f, (lab, c, mk) in names.items():
            g = e[e.fault == f].groupby("length_h")[col].agg(["mean", "std"])
            ax.errorbar(g.index, g["mean"], yerr=g["std"], color=c, marker=mk, ms=4, lw=1.2,
                        capsize=2.5, mec="black", mfc=c, mew=0.6, label=lab)
        ax.axhline(b, color=BLACK, ls="--", lw=1.0, label="No fault (same seeds)")
        ax.set_xlabel("Length of the fault window (hours)")
        ax.set_ylabel(ylab)
        ax.set_xticks([1, 2, 3, 4, 6, 8, 10, 12])
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.98), ncol=3, frameon=True,
               edgecolor="#2C3E50", fancybox=False)
    fig.tight_layout(w_pad=1.2)
    fig.savefig(out / "fig_e4_sweep.pdf", bbox_inches="tight")
    plt.close(fig)


def fmt(m, s):
    return f"{m:.2f} $\\pm$ {s:.2f}"


def tables(res, out):
    d = pd.read_csv(res / "summary.csv")
    rows = []
    e1 = d[d.exp == "E1"]
    e3 = d[d.exp == "E3"]
    cases = [("Fixed V1 (no supervision)", e1[~e1.control]),
             ("Supervised", e1[e1.control]),
             ("Supervised, PLC6 channel lost", e3[e3.fault == "hold:PLC6"]),
             ("Supervised, P4/P5 held off", e3[e3.fault == "off:PLC4"])]
    cols = ["T2_events", "T2_hours_below", "T3_events", "T3_hours_below", "B_unmet_m3",
            "C_unmet_m3", "energy_kwh"]
    lines = []
    for name, g in cases:
        cells = [fmt(g[c].mean(), g[c].std()) if c != "energy_kwh"
                 else f"{g[c].mean():,.0f} $\\pm$ {g[c].std():,.0f}" for c in cols]
        lines.append(name + " & " + " & ".join(cells) + " \\\\")
    (out / "tab_e1_e3.tex").write_text("\n".join(lines) + "\n")
    e2 = d[d.exp == "E2"]
    lines = []
    for season in SEASON_STARTS:
        for ctrl in (False, True):
            g = e2[(e2.season == season) & (e2.control == ctrl)]
            unmet = g.B_unmet_m3 + g.C_unmet_m3
            lines.append(f"{season.capitalize() if not ctrl else ''} & "
                         f"{'Supervised' if ctrl else 'Fixed V1'} & "
                         f"{fmt(g.T2_hours_below.mean(), g.T2_hours_below.std())} & "
                         f"{fmt(g.T3_hours_below.mean(), g.T3_hours_below.std())} & "
                         f"{fmt(unmet.mean(), unmet.std())} \\\\")
        lines.append("\\midrule" if season != list(SEASON_STARTS)[-1] else "")
    (out / "tab_e2.tex").write_text("\n".join(l for l in lines if l) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--out", type=Path, default=Path("../paper"))
    a = ap.parse_args(argv)
    figs, tabs = a.out / "figures", a.out / "tables"
    figs.mkdir(parents=True, exist_ok=True)
    tabs.mkdir(parents=True, exist_ok=True)
    apply_style()
    fig_demand(figs)
    fig_e1_timeseries(a.results, figs)
    fig_e1_bars(a.results, figs)
    fig_e3_timeseries(a.results, figs)
    fig_e4_sweep(a.results, figs)
    tables(a.results, tabs)
    print("figures and tables written to", a.out)


if __name__ == "__main__":
    main()
