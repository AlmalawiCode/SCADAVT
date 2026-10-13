"""Tests for the attack subsystem (scadavt.attacks).

The unit tests run without Docker. A minimal end-to-end MITM check runs only
when SCADAVT_DOCKER_TESTS=1 and Docker + the scadavt-net:2.0 image are present;
it proves the full False Data Injection path (ARP MITM + transparent Modbus
proxy) falsifies a client's reading and recovers cleanly.
"""

from __future__ import annotations

import os
import struct

import pytest

import scadavt.scenario as sc
from scadavt.deploy import compose as dc
from scadavt.attacks import catalog
from scadavt.attacks import deploy as adeploy
from scadavt.attacks import runner as arunner
from scadavt.attacks.handlers import FDIHandler
from scadavt.attacks import proxy
from scadavt.protocols import modbus as mb


# ----------------------------------------------------------------- catalogue
def test_resolve_valid_fdi():
    scen = sc.load()
    plan = dc.plan(scen)
    atk = catalog.Attack.from_spec(
        {"kind": "fdi", "target": "PLC1", "tag": "T2_LVL", "mode": "const",
         "value": 6.5, "start_h": 8, "end_h": 16}, 0)
    r = catalog.resolve(atk, scen, plan)
    assert r["func"] in (3, 4)
    assert r["victim_ip"].startswith("10.10.")
    assert r["router_ip"].endswith(".254")


@pytest.mark.parametrize("spec,msg", [
    ({"kind": "fdi", "target": "PLCX", "tag": "T2_LVL", "start_h": 1, "end_h": 2}, "no such PLC"),
    ({"kind": "fdi", "target": "PLC1", "tag": "NOPE", "start_h": 1, "end_h": 2}, "no tag"),
    ({"kind": "fdi", "target": "PLC6", "tag": "V1_SET_O", "start_h": 1, "end_h": 2}, "not a sensor"),
    ({"kind": "zzz", "target": "PLC1", "tag": "T2_LVL", "start_h": 1, "end_h": 2}, "unknown attack kind"),
    ({"kind": "fdi", "target": "PLC1", "tag": "T2_LVL", "start_h": 5, "end_h": 5}, "end_h"),
])
def test_resolve_rejects_bad_specs(spec, msg):
    scen = sc.load()
    plan = dc.plan(scen)
    with pytest.raises(ValueError) as e:
        catalog.resolve(catalog.Attack.from_spec(spec, 0), scen, plan)
    assert msg in str(e.value)


# ------------------------------------------------------------- FDI handler
def _roundtrip(mode, value, true_val=3.5, addr=10, qty=4):
    h = FDIHandler(func=4, address=addr, mode=mode, value=value)
    req = bytes([4]) + struct.pack(">HH", addr, qty)
    regs = bytearray(qty * 2)
    hi, lo = mb.float_to_regs(true_val)
    regs[0:4] = struct.pack(">HH", hi, lo)
    resp = bytes([4, qty * 2]) + bytes(regs)
    h.on_request(1, 1, 4, req)
    h.enabled = True
    out = h.on_response(1, 1, 4, resp)
    return mb.regs_to_float(list(struct.unpack(">HH", out[2:6]))), h


def test_fdi_modes():
    assert _roundtrip("const", 6.0)[0] == 6.0
    assert abs(_roundtrip("offset", 1.25)[0] - 4.75) < 1e-6
    assert _roundtrip("freeze", 0.0)[0] == 3.5


def test_fdi_disabled_and_out_of_window():
    # disabled: passthrough
    h = FDIHandler(4, 10, "const", 6.0)
    req = bytes([4]) + struct.pack(">HH", 10, 4)
    regs = bytearray(8)
    regs[0:4] = struct.pack(">HH", *mb.float_to_regs(3.5))
    resp = bytes([4, 8]) + bytes(regs)
    h.on_request(1, 1, 4, req)
    assert mb.regs_to_float(list(struct.unpack(">HH", h.on_response(1, 1, 4, resp)[2:6]))) == 3.5
    # enabled but target address not in requested window
    h2 = FDIHandler(4, 99, "const", 6.0)
    h2.enabled = True
    h2.on_request(1, 1, 4, req)
    assert mb.regs_to_float(list(struct.unpack(">HH", h2.on_response(1, 1, 4, resp)[2:6]))) == 3.5
    assert h2.hits == 0


# --------------------------------------------------------------- Modbus frame
def test_proxy_frame_roundtrip():
    pdu = bytes([4, 4]) + struct.pack(">HH", 0x4048, 0x0000)  # FC4, 2 regs
    frame = proxy._rebuild(7, 1, pdu)
    txn, unit, func, got = proxy._split(frame)
    assert (txn, unit, func) == (7, 1, 4)
    assert got == pdu


# ------------------------------------------------------- attacker injection
def test_attacker_service_is_isolated():
    scen = sc.load()
    plan = dc.plan(scen)
    atk = catalog.Attack.from_spec(
        {"id": "a1", "kind": "fdi", "target": "PLC1", "tag": "T2_LVL",
         "start_h": 1, "end_h": 2}, 0)
    svc = adeploy.attacker_services(scen, plan, [atk], "scadavt-net:2.0", "/l:/logs")["attacker_a1"]
    # attached to exactly one field network (never back/ctrl/wan)
    assert list(svc["networks"]) == ["field_D"]
    assert svc["cap_add"] == ["NET_ADMIN", "NET_RAW"]
    assert svc["sysctls"]["net.ipv4.ip_forward"] == "1"
    assert "scadavt.attacks.mitm" in svc["command"]


def test_airgap_marks_all_but_back():
    doc = {"networks": {"wan": {"ipam": {}}, "ctrl": {"ipam": {}}, "back": {"ipam": {}},
                        "field_A": {"ipam": {}}}}
    arunner.airgap_networks(doc)
    assert doc["networks"]["back"].get("internal") is None
    assert doc["networks"]["wan"]["internal"] is True
    assert doc["networks"]["ctrl"]["internal"] is True
    assert doc["networks"]["field_A"]["internal"] is True


# --------------------------------------------- end-to-end MITM (Docker only)
@pytest.mark.skipif(os.environ.get("SCADAVT_DOCKER_TESTS") != "1",
                    reason="set SCADAVT_DOCKER_TESTS=1 to run the Docker MITM test")
def test_mitm_end_to_end(tmp_path):
    import json
    import subprocess
    import textwrap
    import time

    repo = __import__("pathlib").Path(__file__).resolve().parents[1]
    logs = tmp_path / "logs" / "attacks"
    logs.mkdir(parents=True)
    (tmp_path / "server.py").write_text(textwrap.dedent("""
        import asyncio
        from scadavt.protocols import modbus as mb
        bank = mb.DataBank(inputs=64)
        hi, lo = mb.float_to_regs(3.5); bank.tables["A"][10] = hi; bank.tables["A"][11] = lo
        async def main():
            s = await asyncio.start_server(bank.serve, "0.0.0.0", 502)
            async with s: await s.serve_forever()
        asyncio.run(main())
    """))
    (tmp_path / "client.py").write_text(textwrap.dedent("""
        import asyncio
        from scadavt.protocols import modbus as mb
        async def main():
            while True:
                try:
                    c = mb.ModbusClient("10.9.9.11", 502, 1); await c.connect()
                    print("VAL", mb.regs_to_float(await c.read_registers(4, 10, 2)), flush=True)
                    await c.close()
                except Exception as e:
                    print("ERR", e, flush=True)
                await asyncio.sleep(0.5)
        asyncio.run(main())
    """))
    compose = textwrap.dedent(f"""
        name: scadavt_mitm_test
        services:
          server:
            image: scadavt-net:2.0
            entrypoint: []
            working_dir: /app
            environment: {{PYTHONPATH: /app}}
            command: ["python", "server.py"]
            volumes: ["{repo}/scadavt:/app/scadavt:ro", "{tmp_path}/server.py:/app/server.py:ro"]
            networks: {{lab: {{ipv4_address: 10.9.9.11}}}}
          client:
            image: scadavt-net:2.0
            entrypoint: []
            working_dir: /app
            environment: {{PYTHONPATH: /app}}
            command: ["python", "client.py"]
            volumes: ["{repo}/scadavt:/app/scadavt:ro", "{tmp_path}/client.py:/app/client.py:ro"]
            depends_on: [server]
            networks: {{lab: {{ipv4_address: 10.9.9.12}}}}
          attacker:
            image: scadavt-net:2.0
            entrypoint: []
            working_dir: /app
            environment: {{PYTHONPATH: /app}}
            cap_add: [NET_ADMIN, NET_RAW]
            sysctls: {{net.ipv4.ip_forward: "1", net.ipv4.conf.all.rp_filter: "0"}}
            command: ["python", "-m", "scadavt.attacks.mitm", "--victim-ip", "10.9.9.11",
                      "--gw-ip", "10.9.9.12", "--func", "4", "--address", "10",
                      "--mode", "const", "--value", "9.0",
                      "--control", "/logs/attacks/a1.json", "--log", "/logs/attacks/a1.csv"]
            volumes: ["{repo}/scadavt:/app/scadavt:ro", "{tmp_path}/logs:/logs"]
            depends_on: [server]
            networks: {{lab: {{ipv4_address: 10.9.9.13}}}}
        networks:
          lab: {{ipam: {{config: [{{subnet: 10.9.9.0/24}}]}}}}
    """)
    cf = tmp_path / "docker-compose.yml"
    cf.write_text(compose)
    ctl = logs / "a1.json"
    ctl.write_text('{"active": false}')
    cc = ["docker", "compose", "-f", str(cf)]

    def client_vals(since="4s"):
        out = subprocess.run(cc + ["logs", "--since", since, "--no-log-prefix", "client"],
                             capture_output=True, text=True).stdout
        return [float(l.split()[1]) for l in out.splitlines() if l.startswith("VAL")]

    try:
        subprocess.run(cc + ["up", "-d"], check=True, capture_output=True)
        time.sleep(6)
        assert client_vals() and all(abs(v - 3.5) < 1e-3 for v in client_vals()), "baseline not true"
        ctl.write_text('{"active": true}')
        time.sleep(5)
        assert client_vals() and all(abs(v - 9.0) < 1e-3 for v in client_vals()), "injection failed"
        ctl.write_text('{"active": false}')
        time.sleep(7)
        assert client_vals(since="5s") and all(abs(v - 3.5) < 1e-3 for v in client_vals(since="5s")), \
            "did not recover"
    finally:
        subprocess.run(cc + ["down", "-t", "2"], capture_output=True)
