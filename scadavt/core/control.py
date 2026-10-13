"""Configurable control host (proposal V6, §4.3).

One generic control engine, three *placements* chosen by configuration — not by
different code:

* ``mtu``         centralized: the host runs in the MTU and reads/writes PLC
                  registers over the SCADA network (a field-bus client);
* ``plc``         local: the host runs inside a PLC node and reads/writes that
                  device's own image directly, with no MTU in the loop;
* ``distributed`` peer-to-peer: a local host per device plus neighbour reads
                  over the network (deferred past M1, but a valid placement).

The decision logic itself is the domain-neutral rule engine
(``builder.rules.RuleEngine``): the SAME engine and the SAME rules produce the
SAME commands in every placement; only the read source and write sink differ.
The water-specific ``master.control.Supervisor`` is an *example strategy*, not
part of this host.
"""

from __future__ import annotations

from ..builder.rules import RuleEngine

PLACEMENTS = ("mtu", "plc", "distributed")


class ControlHost:
    """Hosts a control engine at a configured placement.

    ``read(kind, comp, attr) -> float | None`` supplies live values;
    :meth:`decide` returns the commands for this cycle as the rule engine's
    ``{comp, ptype, dtype, value}`` dicts. Wiring ``read`` and applying the
    returned commands is the caller's job and is what differs by placement, so
    the engine never needs to know where it runs.
    """

    def __init__(self, engine: RuleEngine, placement: str = "mtu"):
        if placement not in PLACEMENTS:
            raise ValueError(f"placement must be one of {PLACEMENTS}, got {placement!r}")
        self.engine = engine
        self.placement = placement

    @classmethod
    def from_rules(cls, rules_json: list[dict], placement: str = "mtu") -> "ControlHost":
        return cls(RuleEngine.from_json(rules_json), placement)

    def decide(self, read, sim_h: float = 0.0) -> list[dict]:
        return self.engine.evaluate(read, sim_h)

    @property
    def needs_mtu(self) -> bool:
        """Whether this placement requires an MTU node (only centralized does)."""
        return self.placement == "mtu"


def dict_reader(values: dict):
    """Build a ``read(kind, comp, attr)`` over a ``{(kind, comp, attr): value}``
    snapshot — the same signature whether the snapshot came from the network
    (MTU placement) or from a device's local image (PLC placement)."""
    def read(kind: str, comp: str, attr: str):
        return values.get((kind, comp, attr))
    return read
