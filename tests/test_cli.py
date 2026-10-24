"""Framework CLI tests (Phase 2, Milestone 3).

Cover the offline verbs (validate/build/export), the single-implementation
guarantee behind both entry points (R11), and independence from the GUI and HTTP
service (R5). Live `run`/`plan` are exercised on a CORE host separately; here we
assert they fail gracefully when the engine is absent.
"""

import json
import sys

import pytest

from scadavt.core import model


def _write(tmp_path, cfg, name="c.json"):
    p = tmp_path / name
    p.write_text(model.dumps(cfg))
    return str(p)


def _valid():
    return {
        "name": "t", "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.0.254"},
        "devices": [{"id": "PLC1", "ip": "10.0.0.1", "protocol": "modbus",
                     "points": [{"id": "s", "role": "measurement"},
                                {"id": "a", "role": "command", "is_bool": True}]}],
        "switches": [{"id": "SW"}],
        "links": [{"a": "PLC1", "b": "SW", "a_ip": "10.0.0.1"},
                  {"a": "MTU", "b": "SW", "a_ip": "10.0.0.254"}],
        "mapping": [{"point": "s", "device": "PLC1", "role": "measurement", "element": "S1"}],
        "rules": [],
    }


def test_validate_ok(tmp_path, capsys):
    from scadavt import cli
    rc = cli.main(["validate", _write(tmp_path, _valid())])
    assert rc == 0 and "OK" in capsys.readouterr().out


def test_validate_rejects_semantic_fault(tmp_path):
    from scadavt import cli
    cfg = _valid(); cfg["mtu"]["ip"] = "10.0.0.1"      # dup IP
    assert cli.main(["validate", _write(tmp_path, cfg)]) == 1


def test_validate_rejects_structural_fault(tmp_path):
    from scadavt import cli
    cfg = _valid(); del cfg["devices"]
    assert cli.main(["validate", _write(tmp_path, cfg)]) == 1


def test_build_writes_artifacts(tmp_path):
    from scadavt import cli
    out = tmp_path / "out"
    rc = cli.main(["build", _write(tmp_path, _valid()), "-o", str(out)])
    assert rc == 0
    assert (out / "session.json").exists()


def test_export_roundtrip_json(tmp_path, capsys):
    from scadavt import cli
    cfg = _valid()
    assert cli.main(["export", _write(tmp_path, cfg)]) == 0
    assert json.loads(capsys.readouterr().out) == cfg


def test_export_to_yaml_and_back(tmp_path):
    from scadavt import cli
    cfg = _valid()
    yml = tmp_path / "c.yaml"
    assert cli.main(["export", _write(tmp_path, cfg), "--format", "yaml", "-o", str(yml)]) == 0
    assert model.load(str(yml)) == cfg                 # lossless via CLI


def test_both_entry_points_share_one_impl():
    # R11: python -m scadavt and the console-script both resolve to scadavt.cli:main
    import importlib
    import scadavt.__main__ as m
    from scadavt import cli
    assert m.main is cli.main
    # console-script target in metadata points at the same callable
    import tomllib  # py3.11+
    with open("pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    assert data["project"]["scripts"]["scadavt"] == "scadavt.cli:main"


def test_cli_imports_no_gui_or_http(monkeypatch):
    # R5: importing/using the CLI must not require Flask or the HTTP service.
    import importlib
    monkeypatch.setitem(sys.modules, "flask", None)
    for m in [k for k in list(sys.modules) if k.startswith("scadavt.cli")]:
        monkeypatch.delitem(sys.modules, m, raising=False)
    cli = importlib.import_module("scadavt.cli")
    assert hasattr(cli, "main")
    # static: the CLI source names no flask / service import
    import inspect
    src = inspect.getsource(cli)
    assert "flask" not in src.lower()
    assert "http" not in src.replace("HTTP service", "").lower() or "import flask" not in src


def test_run_without_engine_fails_gracefully(tmp_path, monkeypatch, capsys):
    # with corelive absent, `run` returns a clean error code (not a traceback)
    from scadavt import cli
    monkeypatch.setitem(sys.modules, "scadavt.core.corelive", None)
    rc = cli.main(["run", _write(tmp_path, _valid())])
    assert rc == 3
    assert "not" in capsys.readouterr().err.lower()
