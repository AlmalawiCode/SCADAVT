"""L0 — the shared configuration model for SCADAVT (Phase 2).

One model, one file format, used identically by the Python API/CLI and the GUI
designer. This module owns:

* **serialisation** — :func:`load` / :func:`dump` (canonical JSON; YAML accepted
  for human authoring), lossless so a config round-trips unchanged;
* **the canonical schema** — ``schema/config.schema.json`` (structural contract);
* **structural validation** — :func:`validate_structure`, which uses the
  ``jsonschema`` package when it is installed and otherwise falls back to a
  built-in pure-Python check driven by the same schema, so the headless engine
  is **never forced to depend on jsonschema** (design decision D3);
* **typed accessors** — :class:`DeviceSpec` etc. (``TypedDict``) documenting the
  shape, plus small readers like :func:`node_ids`.

Structural validation is only a *precondition*. The authoritative **semantic**
validator remains :func:`scadavt.core.coregen.validate` (design requirement R7);
this module never duplicates those rules.
"""

from __future__ import annotations

import json
import os
from typing import Any, TypedDict

SCHEMA_VERSION = 1
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema", "config.schema.json")


# --------------------------------------------------------------------------
# typed accessors (documentation + light static help; configs stay plain dicts)
# --------------------------------------------------------------------------
class PointSpec(TypedDict, total=False):
    id: str
    role: str          # "measurement" | "command"
    is_bool: bool


class DeviceSpec(TypedDict, total=False):
    id: str
    ip: str
    kind: str
    model: str
    protocol: str
    backchannel: str
    seed: float
    points: list[PointSpec]


class LinkSpec(TypedDict, total=False):
    a: str
    b: str
    a_ip: str
    b_ip: str
    delay_ms: float
    loss: float
    bw: int


class NodeRef(TypedDict, total=False):
    id: str
    ip: str
    model: str


# --------------------------------------------------------------------------
# serialisation — lossless round-trip (R3/R8)
# --------------------------------------------------------------------------
def load(path: str) -> dict:
    """Load a config from JSON or YAML (by extension; JSON tried first)."""
    with open(path, "r") as fh:
        text = fh.read()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import yaml                       # tolerate YAML in a .json-less path
        return yaml.safe_load(text)


def loads(text: str) -> dict:
    return json.loads(text)


def dumps(cfg: dict) -> str:
    """Canonical JSON text: stable key order, 2-space indent. Lossless."""
    return json.dumps(cfg, indent=2, sort_keys=True, ensure_ascii=False)


def dump(cfg: dict, path: str) -> None:
    with open(path, "w") as fh:
        fh.write(dumps(cfg) + "\n")


def with_version(cfg: dict) -> dict:
    """Return a copy carrying the current schema_version (does not mutate)."""
    out = dict(cfg)
    out.setdefault("schema_version", SCHEMA_VERSION)
    return out


# --------------------------------------------------------------------------
# the canonical schema
# --------------------------------------------------------------------------
_SCHEMA_CACHE: dict | None = None


def schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        with open(SCHEMA_PATH) as fh:
            _SCHEMA_CACHE = json.load(fh)
    return _SCHEMA_CACHE


# --------------------------------------------------------------------------
# structural validation — jsonschema if present, else built-in fallback
# --------------------------------------------------------------------------
def validate_structure(cfg: Any) -> list[str]:
    """Return a list of structural problems (empty == structurally OK).

    Prefers the ``jsonschema`` package for full Draft-7 checking; if it is not
    installed, uses a built-in check of the schema's required keys and declared
    types so the engine has no hard dependency on jsonschema."""
    try:
        import jsonschema                                      # optional
    except Exception:
        return _builtin_check(cfg, schema(), "$")
    validator = jsonschema.Draft7Validator(schema())
    out = []
    for e in sorted(validator.iter_errors(cfg), key=lambda e: list(e.path)):
        loc = "$" + "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in e.path)
        out.append(f"{loc}: {e.message}")
    return out


_JSON_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float),
}


def _builtin_check(value: Any, sch: dict, loc: str) -> list[str]:
    """Minimal recursive structural check against a (subset of) JSON Schema:
    type, required, enum, properties, items. Enough to guard the config shape
    without the jsonschema dependency."""
    issues: list[str] = []
    t = sch.get("type")
    if t:
        py = _JSON_TYPES.get(t)
        # bool is a subclass of int — keep them distinct
        if t == "integer" and isinstance(value, bool):
            issues.append(f"{loc}: expected integer, got boolean")
            return issues
        if t == "number" and isinstance(value, bool):
            issues.append(f"{loc}: expected number, got boolean")
            return issues
        if py and not isinstance(value, py):
            issues.append(f"{loc}: expected {t}, got {type(value).__name__}")
            return issues
    if "enum" in sch and value not in sch["enum"]:
        issues.append(f"{loc}: {value!r} not in {sch['enum']}")
    if t == "object" and isinstance(value, dict):
        for req in sch.get("required", []):
            if req not in value:
                issues.append(f"{loc}: missing required key {req!r}")
        for key, subsch in sch.get("properties", {}).items():
            if key in value:
                issues += _builtin_check(value[key], subsch, f"{loc}[{key!r}]")
    if t == "array" and isinstance(value, list):
        item_sch = sch.get("items")
        if isinstance(item_sch, dict):
            for i, item in enumerate(value):
                issues += _builtin_check(item, item_sch, f"{loc}[{i}]")
    return issues


# --------------------------------------------------------------------------
# small readers
# --------------------------------------------------------------------------
def node_ids(cfg: dict) -> list[str]:
    """Every addressable/placeable node id in the config (devices, mtu, routers,
    switches) — handy for designers and round-trip diffs."""
    ids = [d["id"] for d in cfg.get("devices", []) if "id" in d]
    if cfg.get("mtu", {}).get("id"):
        ids.append(cfg["mtu"]["id"])
    ids += [r["id"] for r in cfg.get("routers", []) if "id" in r]
    ids += [s["id"] for s in cfg.get("switches", []) if "id" in s]
    return ids
