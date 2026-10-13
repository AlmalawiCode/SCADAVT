"""Synchronized, reproducible experiment bundle (proposal §7.1; M3).

Assembles one bundle on a single reference clock:

* ``trace.csv``      — the process trace: measurements, commands and actual
                       actuator states per cycle;
* ``labels.csv``     — the two-dimensional ground truth: ``attack_window`` (from
                       the schedule) and ``malicious_confirmed`` (from evidence);
* ``metadata.json``  — configuration, control placement, timing mode, seeds,
                       tool versions, attack schedule, and the pcap reference;
* the pcap(s)        — captured separately (``capture.py``), referenced by path.

Everything is timestamped on the same clock so the network capture, the process
trace and the labels line up sample-by-sample.
"""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import labels as lbl


@dataclass
class ExperimentRecorder:
    metadata: dict = field(default_factory=dict)
    rows: list = field(default_factory=list)          # [{t, <value cols...>}]
    windows: list = field(default_factory=list)       # list[labels.AttackWindow]
    confirmed: dict = field(default_factory=dict)     # {t: evidence}
    pcaps: list = field(default_factory=list)          # paths

    def record(self, t: float, values: dict) -> None:
        row = {"t": t}
        row.update(values)
        self.rows.append(row)

    def confirm(self, t: float, evidence: str) -> None:
        self.confirmed[t] = evidence

    def _labels(self) -> list[lbl.Sample]:
        samples = [lbl.Sample(t=r["t"]) for r in self.rows]
        return lbl.label(samples, self.windows, self.confirmed)

    def write_bundle(self, out_dir: str | Path) -> dict:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        # trace.csv
        cols = ["t"] + sorted({k for r in self.rows for k in r if k != "t"})
        with open(out / "trace.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in self.rows:
                w.writerow(r)
        # labels.csv (both dimensions, kept distinct)
        samples = self._labels()
        with open(out / "labels.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t", "attack_window", "window_kind",
                        "malicious_confirmed", "evidence"])
            for s in samples:
                w.writerow([s.t, int(s.attack_window), s.window_kind,
                            int(s.malicious_confirmed), s.evidence])
        # copy any pcaps in
        pcap_names = []
        for p in self.pcaps:
            p = Path(p)
            if p.exists():
                shutil.copy(p, out / p.name)
                pcap_names.append(p.name)
        # metadata.json
        meta = dict(self.metadata)
        meta.update({
            "samples": len(self.rows),
            "label_summary": lbl.summary(samples),
            "attack_windows": [vars(w) for w in self.windows],
            "pcaps": pcap_names,
            "files": ["trace.csv", "labels.csv", "metadata.json"] + pcap_names,
        })
        (out / "metadata.json").write_text(json.dumps(meta, indent=2))
        return {"dir": str(out), "metadata": meta}
