"""Deterministic register write ownership & priority (register milestone M4).

Multiple independent writers can target one register — the MTU's control logic, a
PLC's local logic, and external adapters. Resolution is explicit and
deterministic, never implicit:

* if a register declares an **owner**, only that writer class may set it (writes
  from others are ignored);
* otherwise the **highest-priority** writer wins; on equal priority the last
  submitted write wins (stable order).

Writer classes and default priorities (configurable): ``process`` (external
adapter) > ``plc`` (local control) > ``mtu`` (central master). This makes a
physical producer authoritative over a read-only register while a command
register can be reserved to the MTU by declaring ``owner: mtu``.
"""

from __future__ import annotations

DEFAULT_PRIORITY = {"process": 30, "plc": 20, "mtu": 10}


class WriteArbiter:
    def __init__(self, priority: dict | None = None):
        self.priority = dict(DEFAULT_PRIORITY)
        if priority:
            self.priority.update(priority)

    def allows(self, owner, writer) -> bool:
        """True if ``writer`` may write a register whose declared owner is
        ``owner`` (None/``any`` means unreserved)."""
        return owner in (None, "any") or owner == writer

    def resolve(self, owner, proposals):
        """``proposals`` = list of ``(writer, value)`` in submission order.
        Returns the single winning value, or None if no proposal may write."""
        cand = [(w, v) for (w, v) in proposals if self.allows(owner, w)]
        if not cand:
            return None
        best_v, best_pri = None, float("-inf")
        for w, v in cand:
            pri = self.priority.get(w, 0)
            if pri >= best_pri:                 # >= : later equal-priority wins (deterministic)
                best_v, best_pri = v, pri
        return best_v
