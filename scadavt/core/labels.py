"""Dataset ground-truth labelling (proposal V6, §7.1; refinement 2).

A sample carries **two independent label dimensions**, which must not be
conflated:

* ``attack_window`` — whether an attack was *scheduled active* at that time
  (from the experiment's attack schedule). This marks intent/opportunity.
* ``malicious_confirmed`` — whether the sample is *confirmed malicious traffic*
  from observed evidence (a packet from an attacker source, or a command that
  was actually altered). Not every packet inside an attack window is malicious:
  benign supervisory traffic continues during a DoS or an MITM window.

Keeping them separate lets intrusion-detection work be evaluated honestly:
window-based labels over-count; confirmation-based labels require evidence.
Nothing here is domain-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AttackWindow:
    kind: str           # e.g. "dos", "integrity"
    target: str         # node / link / device id
    start: float        # seconds on the common timeline
    end: float

    def active(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass
class Sample:
    t: float                      # timestamp on the common reference clock
    attack_window: bool = False   # dimension (a): a scheduled attack was active
    window_kind: str = ""         # which attack window, if any
    malicious_confirmed: bool = False  # dimension (b): confirmed malicious
    evidence: str = ""            # why it was confirmed (src match, altered cmd, ...)
    meta: dict = field(default_factory=dict)


def in_window(t: float, windows: list[AttackWindow]) -> AttackWindow | None:
    for w in windows:
        if w.active(t):
            return w
    return None


def label(samples: list[Sample], windows: list[AttackWindow],
          confirmed: dict | None = None) -> list[Sample]:
    """Annotate ``samples`` in place with both label dimensions.

    ``confirmed`` maps a sample timestamp (as returned by the capture analysis)
    to an evidence string; only those samples get ``malicious_confirmed=True``.
    A sample can be inside an attack window yet NOT confirmed malicious.
    """
    confirmed = confirmed or {}
    for s in samples:
        w = in_window(s.t, windows)
        s.attack_window = w is not None
        s.window_kind = w.kind if w else ""
        if s.t in confirmed:
            s.malicious_confirmed = True
            s.evidence = confirmed[s.t]
    return samples


def summary(samples: list[Sample]) -> dict:
    n = len(samples)
    win = sum(s.attack_window for s in samples)
    mal = sum(s.malicious_confirmed for s in samples)
    # malicious samples should be a subset of window samples
    mal_outside = sum(s.malicious_confirmed and not s.attack_window for s in samples)
    return {"samples": n, "in_window": win, "confirmed_malicious": mal,
            "benign_in_window": win - sum(s.attack_window and s.malicious_confirmed
                                          for s in samples),
            "confirmed_outside_window": mal_outside}
