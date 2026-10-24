"""L0 shared-model tests (Phase 2, Milestone 1).

Cover serialisation round-trip (R3/R8 foundation), the canonical schema, and the
structural validator — including that the built-in fallback agrees with the
jsonschema package when it is installed, so the headless engine needs no
jsonschema dependency (D3).
"""

import json

import pytest

from scadavt.core import model


def _sample():
    return {
        "schema_version": 1,
        "name": "demo", "netmask": 24,
        "control": {"placement": "mtu"},
        "mtu": {"id": "MTU", "ip": "10.0.1.254"},
        "devices": [
            {"id": "PLC1", "ip": "10.0.1.1", "protocol": "modbus", "seed": 2.0,
             "points": [{"id": "s", "role": "measurement", "is_bool": False},
                        {"id": "a", "role": "command", "is_bool": True}]},
            {"id": "PLC2", "ip": "10.0.2.1", "protocol": "modbus",
             "points": [{"id": "s", "role": "measurement"}]},
        ],
        "switches": [{"id": "SWA"}, {"id": "SWB"}],
        "routers": [{"id": "R1", "model": "router"}],
        "links": [
            {"a": "R1", "b": "SWA", "a_ip": "10.0.1.250"},
            {"a": "R1", "b": "SWB", "a_ip": "10.0.2.250"},
            {"a": "MTU", "b": "SWA", "a_ip": "10.0.1.254"},
            {"a": "PLC1", "b": "SWA", "a_ip": "10.0.1.1"},
            {"a": "PLC2", "b": "SWB", "a_ip": "10.0.2.1", "loss": 5.0, "delay_ms": 20},
        ],
        "mapping": [
            {"point": "s", "device": "PLC1", "role": "measurement", "element": "S1"},
            {"point": "a", "device": "PLC1", "role": "command", "element": "A1"},
        ],
        "rules": [{"id": "r1", "if": [], "then": []}],
        # an unknown-but-supported overlay key must survive round-trip
        "apps": {"water": {"inp": "net.inp"}},
    }


def test_roundtrip_lossless_json(tmp_path):
    cfg = _sample()
    p = tmp_path / "c.json"
    model.dump(cfg, str(p))
    back = model.load(str(p))
    assert back == cfg                       # nothing lost, incl. overlay + link opts


def test_roundtrip_lossless_dumps_loads():
    cfg = _sample()
    assert model.loads(model.dumps(cfg)) == cfg


def test_roundtrip_via_yaml(tmp_path):
    cfg = _sample()
    p = tmp_path / "c.yaml"
    import yaml
    p.write_text(yaml.safe_dump(cfg))
    assert model.load(str(p)) == cfg


def test_schema_file_is_valid_json_and_requires_devices():
    s = model.schema()
    assert s["type"] == "object"
    assert "devices" in s["required"]


def test_valid_config_passes_structure():
    assert model.validate_structure(_sample()) == []


def test_missing_devices_fails_structure():
    cfg = _sample(); del cfg["devices"]
    issues = model.validate_structure(cfg)
    assert any("devices" in i for i in issues)


def test_wrong_type_fails_structure():
    cfg = _sample(); cfg["netmask"] = "twenty-four"
    issues = model.validate_structure(cfg)
    assert any("netmask" in i for i in issues)


def test_bad_enum_fails_structure():
    cfg = _sample(); cfg["control"]["placement"] = "nonsense"
    issues = model.validate_structure(cfg)
    assert issues != []


def test_builtin_fallback_matches_jsonschema_presence():
    """Whether or not jsonschema is installed, a good config passes and a broken
    one fails — the fallback must not disagree on these basics."""
    good, bad = _sample(), _sample()
    del bad["devices"][0]["id"]              # device missing required id
    assert model.validate_structure(good) == []
    assert model.validate_structure(bad) != []


def test_builtin_check_directly():
    # exercise the fallback path regardless of jsonschema availability
    good = _sample()
    assert model._builtin_check(good, model.schema(), "$") == []
    bad = _sample(); bad["devices"] = "not a list"
    assert model._builtin_check(bad, model.schema(), "$") != []


def test_node_ids():
    ids = model.node_ids(_sample())
    assert set(ids) == {"PLC1", "PLC2", "MTU", "R1", "SWA", "SWB"}


def test_with_version_sets_default_without_mutating():
    cfg = {"devices": []}
    out = model.with_version(cfg)
    assert out["schema_version"] == model.SCHEMA_VERSION
    assert "schema_version" not in cfg
