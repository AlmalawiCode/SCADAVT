"""Residential water-demand model.

The demand of every sub-area is recomputed at the start of each simulated hour
from three ingredients:

1. Daily use per person. For the current week we take the reported average
   daily use per person of a window of weeks centred on it (the week itself and
   ``half_window`` weeks before and after), compute the window mean and sample
   standard deviation, and draw the day's per-person use from a normal
   distribution with these parameters. The window captures seasonal change,
   and the random draw adds day-to-day variability.
2. A diurnal profile that gives the share of the daily volume used in each
   hour of the day (the shares sum to one).
3. Occupancy. Every hour a random fraction (at most ``max_away``) of the
   people of each sub-area is away from home.

The demand of a sub-area in litres per minute is
    people_present * daily_use * hourly_share / 60.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Default diurnal profile: share of the daily volume used in each hour (sums
# to 1). The day is split into four periods - night (0-5 h) 10 %, morning
# (6-11 h) 35 %, afternoon (12-17 h) 20 % and evening (18-23 h) 35 % - with
# two peak hours in the morning and in the evening. The peak-hour share is set
# to 2.06/24, so that the peak hour is 2.06 times the mean hour, the
# peak-hour factor measured with smart meters by Gurung et al. (2014).
_PEAK = 2.06 / 24
_REST = (0.35 - 2 * _PEAK - 0.05) / 3
DEFAULT_PROFILE = (
    [0.10 / 6] * 6
    + [_PEAK, _PEAK, 0.05, _REST, _REST, _REST]
    + [0.20 / 6] * 6
    + [_PEAK, _PEAK, 0.05, _REST, _REST, _REST]
)


def load_series(path: str | Path) -> list[tuple[dt.date, float]]:
    """Read ``date,litres_per_person_per_day`` rows (ISO dates)."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append((dt.date.fromisoformat(r["date"]),
                         float(r["litres_per_person_per_day"])))
    rows.sort()
    return rows


def load_profile(path: str | Path) -> list[float]:
    vals = [0.0] * 24
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            vals[int(r["hour"])] = float(r["multiplier"])
    s = sum(vals)
    return [v / s for v in vals]


@dataclass
class DemandModel:
    series: list[tuple[dt.date, float]]
    population: dict[str, int]
    start: dt.date
    profile: list[float] = field(default_factory=lambda: list(DEFAULT_PROFILE))
    half_window: int = 2
    max_away: float = 0.10
    seed: int = 1
    _day_cache: dict[int, float] = field(default_factory=dict, init=False)

    def __post_init__(self):
        self.rng = np.random.default_rng(self.seed)
        s = sum(self.profile)
        self.profile = [p / s for p in self.profile]

    def window_stats(self, day: dt.date) -> tuple[float, float]:
        """Mean and sample s.d. of the reported values around ``day``."""
        dates = [d for d, _ in self.series]
        # index of the latest reported period that starts on or before day
        idx = max(0, max((i for i, d in enumerate(dates) if d <= day), default=0))
        lo = max(0, idx - self.half_window)
        hi = min(len(self.series), idx + self.half_window + 1)
        x = np.array([v for _, v in self.series[lo:hi]], dtype=float)
        sd = float(x.std(ddof=1)) if len(x) > 1 else 0.0
        return float(x.mean()), sd

    def daily_use(self, day_index: int) -> float:
        """Per-person use (L/day) of simulated day ``day_index`` (cached)."""
        if day_index not in self._day_cache:
            day = self.start + dt.timedelta(days=day_index)
            mu, sd = self.window_stats(day)
            self._day_cache[day_index] = max(0.3 * mu, float(self.rng.normal(mu, sd)))
        return self._day_cache[day_index]

    def hourly_demands(self, sim_seconds: float) -> dict[str, float]:
        """Demand (L/min) of every sub-area for the hour containing ``sim_seconds``."""
        hour_index = int(sim_seconds // 3600)
        day_index, hour = divmod(hour_index, 24)
        use = self.daily_use(day_index)
        out = {}
        for node, people in self.population.items():
            present = people * (1.0 - self.rng.uniform(0.0, self.max_away))
            out[node] = present * use * self.profile[hour] / 60.0
        return out
