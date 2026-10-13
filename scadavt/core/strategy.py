"""Control strategies as a configurable, pluggable choice (M3).

A *strategy* is any object with ``decide(read, sim_h) -> list[command dict]``,
where a command dict is ``{comp, ptype, dtype, value}`` (the rule-engine form).
The domain-neutral rule engine is one strategy; a researcher's own controller is
another. The thesis's water ``Supervisor`` (pump groups + the V1 flow algorithm)
is wrapped as an *example* strategy in ``scadavt.examples.water_strategy`` — it
is selected by configuration, never baked into the core.
"""

from __future__ import annotations

from ..builder.rules import RuleEngine


class RuleStrategy:
    """The generic IF/THEN rule engine presented as a strategy."""

    def __init__(self, engine: RuleEngine):
        self.engine = engine

    @classmethod
    def from_rules(cls, rules_json: list[dict]) -> "RuleStrategy":
        return cls(RuleEngine.from_json(rules_json))

    def decide(self, read, sim_h: float = 0.0) -> list[dict]:
        return self.engine.evaluate(read, sim_h)


#: registry of built-in strategies (configuration selects by name)
STRATEGIES = {"rules": RuleStrategy}
