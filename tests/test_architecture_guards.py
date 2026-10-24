"""Architecture guard tests (Phase 2, Milestone 4) — freeze the Phase-2 invariant
in CI so a regression fails the build, not a review.

Covers:
* **T-R4 / R5 / R6** dependency direction — the shared engine (L0–L2) and the
  Python CLI import no GUI, no HTTP service, and no domain (water/EPANET) code,
  both *statically* (source scan) and at *runtime* (import with those modules
  forced absent).
* **T-R11** — the two CLI entry points resolve to one implementation.

The generic designer and HTTP service do not exist yet (Milestones 5–6); their
own guards (T-R6 designer-level, T-R10) arrive with them. These tests assert the
lower stack is clean *now* so those layers are built on a sound base.
"""

import ast
import importlib
import os
import sys

import pytest

import scadavt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(scadavt.__file__)))

# modules that form the shared, headless stack (L0–L2) + the CLI (L3a)
LOWER_STACK_FILES = [
    "scadavt/cli.py",
    "scadavt/__main__.py",
] + [
    os.path.join("scadavt/core", f)
    for f in sorted(os.listdir(os.path.join(ROOT, "scadavt", "core")))
    if f.endswith(".py")
]

# import prefixes a headless engine / CLI must never pull in
FORBIDDEN = ("flask", "labui", "epanet", "scadavt.webui", "scadavt.apps.water",
             "aiohttp", "fastapi", "uvicorn", "scadavt.service")


def _imported_names(path: str):
    with open(os.path.join(ROOT, path)) as fh:
        tree = ast.parse(fh.read(), filename=path)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            # resolve a relative 'from . import x' within scadavt.core
            if node.level and path.startswith("scadavt/core"):
                names.append("scadavt.core." + node.module)
            else:
                names.append(node.module)
    return names


@pytest.mark.parametrize("path", LOWER_STACK_FILES)
def test_no_forbidden_imports_in_lower_stack(path):
    """T-R4/R5/R6 static: no L0–L2 / CLI file imports a GUI, HTTP, or domain module."""
    for name in _imported_names(path):
        for bad in FORBIDDEN:
            assert not (name == bad or name.startswith(bad + ".")), \
                f"{path} imports forbidden module {name!r}"


def test_engine_imports_with_gui_and_http_absent(monkeypatch):
    """T-R4/R5 runtime: the model, validator and CLI import and work with Flask,
    aiohttp, fastapi all forced absent."""
    for mod in ("flask", "aiohttp", "fastapi"):
        monkeypatch.setitem(sys.modules, mod, None)
    for loaded in [m for m in list(sys.modules)
                   if m.startswith(("scadavt.core.model", "scadavt.core.validation",
                                    "scadavt.cli"))]:
        monkeypatch.delitem(sys.modules, loaded, raising=False)
    model = importlib.import_module("scadavt.core.model")
    validation = importlib.import_module("scadavt.core.validation")
    cli = importlib.import_module("scadavt.cli")
    # and they actually function
    cfg = {"devices": [{"id": "P", "ip": "10.0.0.1",
                        "points": [{"id": "s", "role": "measurement"}]}],
           "mtu": {"id": "M", "ip": "10.0.0.2"},
           "switches": [{"id": "SW"}],
           "links": [{"a": "P", "b": "SW", "a_ip": "10.0.0.1"},
                     {"a": "M", "b": "SW", "a_ip": "10.0.0.2"}],
           "mapping": [{"point": "s", "device": "P", "role": "measurement", "element": "S"}]}
    assert model.validate_structure(cfg) == []
    assert isinstance(validation.validate(cfg), list)
    assert hasattr(cli, "main")


def test_core_does_not_import_the_cli_or_tools():
    """The engine must not depend upward on the CLI (L3a)."""
    for path in LOWER_STACK_FILES:
        if path.startswith("scadavt/core"):
            for name in _imported_names(path):
                assert not name.startswith("scadavt.cli"), f"{path} imports the CLI"


def test_both_cli_entry_points_one_impl():
    """T-R11 (also asserted in test_cli; kept here as an architecture invariant)."""
    import scadavt.__main__ as m
    from scadavt import cli
    assert m.main is cli.main
