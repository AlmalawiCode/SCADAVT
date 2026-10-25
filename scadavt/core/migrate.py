"""Convert a legacy role-based config to the native register model.

The transitional alias lets legacy ``points``/``mapping``/``rules`` still run, but
the project converges on **one** register-based model (register milestone M3).
:func:`migrate` rewrites a legacy config to native ``registers`` + ``logic`` while
preserving device identity, addressing, control relationships and runtime
behaviour. It is **idempotent**: an already-register config is returned
unchanged, so ``migrate(migrate(cfg)) == migrate(cfg)``.

    python -m scadavt migrate old.json -o new.json
"""

from __future__ import annotations

import copy

from . import fieldbus as fb


def _then_value(v) -> float:
    """Legacy actuator command value -> register value."""
    if isinstance(v, str):
        return 1.0 if v.strip().lower() in ("open", "on", "1", "true") else 0.0
    return float(v)


def migrate(cfg: dict) -> dict:
    """Return a copy of ``cfg`` with every device using native ``registers`` and
    control expressed as ``logic`` (legacy ``mapping``/``rules`` removed)."""
    out = copy.deepcopy(cfg)

    # 1) device points -> registers (area/type via the transitional lowering)
    for d in out.get("devices", []):
        if d.get("registers") or not d.get("points"):
            continue                              # already native, or nothing to do
        regs = []
        for p in d["points"]:
            is_bool = bool(p.get("is_bool", False))
            reg = {"id": p["id"], "area": fb._legacy_area(p["role"], is_bool),
                   "data_type": "bool" if is_bool else "float32"}
            if "address" in p:
                reg["address"] = int(p["address"])
            regs.append(reg)
        d.pop("points", None)
        d["registers"] = regs

    # 2) mapping + rules -> logic (preserve control relationships)
    mapping = out.get("mapping", [])
    rules = out.get("rules", [])
    if mapping or rules:
        elem2reg = {m.get("element", m["point"]): (m["device"], m["point"]) for m in mapping}
        logic = list(out.get("logic", []))
        for r in rules:
            conds, acts = [], []
            for c in r.get("if", []):
                ref = elem2reg.get(c.get("comp"))
                if ref:
                    conds.append({"device": ref[0], "register": ref[1],
                                  "op": c.get("op", "equal"), "value": c.get("value")})
            for a in r.get("then", []):
                ref = elem2reg.get(a.get("comp"))
                if ref:
                    acts.append({"device": ref[0], "register": ref[1],
                                 "set": _then_value(a.get("value"))})
            if conds and acts:
                logic.append({"id": r.get("id", f"r{len(logic)+1}"),
                              "if": conds, "then": acts})
        out.pop("mapping", None)
        out.pop("rules", None)
        if logic:
            out["logic"] = logic
    return out


def is_legacy(cfg: dict) -> bool:
    """True if ``cfg`` still uses any legacy role-based construct."""
    if any(d.get("points") for d in cfg.get("devices", [])):
        return True
    return bool(cfg.get("mapping") or cfg.get("rules"))
