"""L1 — the single validation entry point for SCADAVT (Phase 2).

Both workflows (Python API/CLI and the GUI designer) call **this** function, so
they always agree (requirement R2). Validation is two stages with a clear
authority order (requirement R7):

1. **structural** — :func:`scadavt.core.model.validate_structure` against the
   canonical JSON Schema (shape/types/required). A precondition.
2. **semantic (authoritative)** — :func:`scadavt.core.coregen.validate`
   (duplicate IPs, dangling link endpoints, mapping/point references, placement,
   addressing). A config is valid **iff this stage returns no issues**.

This module adds no rules of its own; it only *composes* and *labels* the two
stages. If the structure is broken the semantic stage is skipped (its rules
assume a well-formed config), and the structural issues are returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import coregen, model


@dataclass
class Result:
    """Outcome both the CLI and the GUI render. ``ok`` is the single source of
    truth for 'is this config runnable?'."""
    structural: list[str] = field(default_factory=list)
    semantic: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.structural and not self.semantic

    @property
    def issues(self) -> list[str]:
        """Flat, labelled list for display."""
        return [f"structure: {m}" for m in self.structural] + \
               [f"semantic: {m}" for m in self.semantic]

    def __bool__(self) -> bool:
        return self.ok


def check(cfg: dict) -> Result:
    """Run both stages (semantic only if structure holds) and return a Result."""
    structural = model.validate_structure(cfg)
    if structural:
        return Result(structural=structural)       # semantic rules assume good shape
    return Result(semantic=coregen.validate(cfg))


def validate(cfg: dict) -> list[str]:
    """The flat list of labelled issues (empty == valid). The one call both the
    CLI and the GUI use."""
    return check(cfg).issues


def is_valid(cfg: dict) -> bool:
    return check(cfg).ok


class ValidationError(ValueError):
    def __init__(self, result: Result):
        self.result = result
        super().__init__("; ".join(result.issues))


def ensure_valid(cfg: dict) -> dict:
    """Return ``cfg`` if valid, else raise :class:`ValidationError`."""
    r = check(cfg)
    if not r.ok:
        raise ValidationError(r)
    return cfg
