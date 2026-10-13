"""Plant model = hydraulic engine + demand model, advanced on a simulated clock."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from ..scenario import Scenario
from .demand import DemandModel, load_profile, load_series
from .engine import WaterNetwork

PKG = Path(__file__).resolve().parents[1]
DATA = PKG.parent / "data"


def default_series_path() -> Path:
    """Path to the consumption series. ``SCADAVT_CONSUMPTION`` overrides it, so a
    researcher (or CI) can point at their own copy without moving files."""
    env = os.environ.get("SCADAVT_CONSUMPTION")
    return Path(env) if env else DATA / "consumption_per_person.csv"


class Plant:
    """The controlled environment: a water network with time-varying demand."""

    def __init__(self, scenario: Scenario, start: dt.date | None = None, seed: int = 1,
                 hyd_step: int = 300, series_path: Path | None = None,
                 profile_path: Path | None = None):
        self.scenario = scenario
        self.net = WaterNetwork(scenario.epanet_file, hyd_step=hyd_step)
        series = load_series(series_path or default_series_path())
        profile_file = profile_path or (DATA / "diurnal_pattern.csv")
        kw = {}
        if profile_file.exists():
            kw["profile"] = load_profile(profile_file)
        self.demand = DemandModel(series, scenario.population,
                                  start or series[len(series) // 2][0], seed=seed, **kw)
        self._hour = -1
        self._apply_demands()

    def _apply_demands(self) -> None:
        hour = int(self.net.t // 3600)
        if hour != self._hour:
            self._hour = hour
            for node, lpm in self.demand.hourly_demands(self.net.t).items():
                self.net.set_demand(node, lpm)

    def advance(self, seconds: float) -> int:
        """Advance in hydraulic steps, refreshing the demand every hour."""
        target = self.net.t + seconds
        while self.net.t < target:
            self._apply_demands()
            step = min(self.net.hyd_step, target - self.net.t)
            before = self.net.t
            self.net.advance(step)
            if self.net.t == before:
                break
        self._apply_demands()
        return self.net.t

    def close(self):
        self.net.close()
