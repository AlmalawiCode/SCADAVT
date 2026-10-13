"""Smoke test for the guided demo module: it imports without side effects and
its config generates a valid session. The live loops are covered by the M1/M2
core tests, so we don't re-run them here."""

from __future__ import annotations

from scadavt.core import coregen
from scadavt.examples import demo


def test_demo_config_is_valid_and_generates(tmp_path):
    cfg = demo.demo_config()
    assert coregen.validate(cfg) == []
    files = coregen.write_session(cfg, tmp_path / "run")
    assert set(files) >= {"session.json", "core_session.py", "run_devices.sh",
                          "gate_endpoints.ini", "gate_points.ini"}


def test_demo_part_a_runs(capsys):
    demo.part_a_generate()
    assert "validation: OK" in capsys.readouterr().out
