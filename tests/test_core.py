"""Unit tests of the protocols, the demand model, the control logic and the plant."""

import datetime as dt
import math

import pytest

from scadavt import scenario as sc
from scadavt.master.control import Supervisor
from scadavt.protocols import iomodules as io
from scadavt.protocols import modbus as mb
from scadavt.protocols import wdsproto as wp
from scadavt.wds.demand import DEFAULT_PROFILE, DemandModel


def test_iomodules_roundtrip():
    m = io.IOMessage(7, io.FC_WRITE, "T2_LVL", 3.25)
    raw = m.pack()
    assert len(raw) == 19
    back = io.IOMessage.unpack(raw)
    assert (back.transaction, back.function, back.pid) == (7, io.FC_WRITE, "T2_LVL")
    assert math.isclose(back.data, 3.25)


def test_iomodules_pid_too_long():
    with pytest.raises(ValueError):
        io.IOMessage(1, io.FC_READ, "ABCDEFGHIJK").pack()


def test_wds_roundtrip():
    m = wp.WDSMessage("P4", wp.FC_CONTROL, wp.PT_LINK, wp.DT_STATUS, 1.0)
    raw = m.pack()
    assert len(raw) == 17
    assert wp.WDSMessage.unpack(raw) == m


def test_modbus_float_and_bits():
    assert math.isclose(mb.regs_to_float(mb.float_to_regs(1234.5)), 1234.5)
    bits = [True, False, True, True, False, False, False, False, True]
    assert mb.unpack_bits(mb.pack_bits(bits), len(bits)) == bits


def test_window_statistics_match_original_example():
    # The original SCADAVT example: five weeks around 23-01-2009
    series = [(dt.date(2009, 1, 9), 143), (dt.date(2009, 1, 16), 172), (dt.date(2009, 1, 23), 188),
              (dt.date(2009, 1, 30), 207), (dt.date(2009, 2, 6), 241)]
    model = DemandModel(series, {"X": 1}, dt.date(2009, 1, 23))
    mu, sd = model.window_stats(dt.date(2009, 1, 23))
    assert round(mu, 1) == 190.2
    assert round(sd, 2) == 36.83  # sample standard deviation (n - 1)


def test_profile_sums_to_one_and_peak_factor():
    assert math.isclose(sum(DEFAULT_PROFILE), 1.0)
    assert math.isclose(max(DEFAULT_PROFILE) * 24, 2.06)


def test_register_map_of_plc1():
    s = sc.load()
    p1 = s.plcs["PLC1"]
    t = p1.tag("T2_LVL")
    assert (t.reg_type, t.address, t.direction) == ("A", 0, "i")
    assert p1.tag("V35A_ST_O").reg_type == "C"
    assert len(s.plcs) == 33
    assert sum(len(v) for v in s.fields.values()) == 33


def test_supervisor_hysteresis_and_balance():
    s = sc.load()
    sup = Supervisor(s.control, s.doc["tank_max_level"])
    low = {k: 0.5 for k in sup.required_reads()}
    cmds = {(c.plc, c.tag): c.value for c in sup.decide(low)}
    assert cmds[("PLC4", "P4_ST_O")] == 1.0          # T2 low -> pumps on
    v1 = cmds[("PLC6", "V1_SET_O")]
    assert 0.0 <= v1 <= s.control["v1"]["max_flow"]
    # equal tanks: only the volume-balance term, (A3 (f - b) + A2 c) / (A2 + A3) = c / 2
    assert math.isclose(v1, 0.25)
    # T2 fuller than T3 moves water towards T3
    assert sup.v1_flow(5.0, 2.0, 0.0, 0.0, 0.0) > sup.v1_flow(3.5, 3.5, 0.0, 0.0, 0.0)


def test_missing_values_keep_previous_decision():
    s = sc.load()
    sup = Supervisor(s.control, s.doc["tank_max_level"])
    first = {(c.plc, c.tag): c.value for c in sup.decide({k: 0.5 for k in sup.required_reads()})}
    again = {(c.plc, c.tag): c.value for c in sup.decide({})}
    assert first == again


def test_offline_run_is_reproducible():
    from scadavt.experiments.offline import run
    a = run(6, True, seed=3, start=dt.date(2025, 1, 23))
    b = run(6, True, seed=3, start=dt.date(2025, 1, 23))
    assert len(a) == 72
    assert a["T2_level"].equals(b["T2_level"])
    assert a.filter(like="_level").min().min() >= 0.0
