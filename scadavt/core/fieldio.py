"""PLC I/O modules and emulated field devices (sensors / actuators).

A real Modbus PLC or remote-I/O head carries **I/O modules** — digital input
(DI), digital output (DO), analog input (AI), analog output (AO) — each with a
number of channels, and exposes every channel in one of the four Modbus tables
(Modbus Application Protocol spec, §4.3 data model):

    DI channel -> discrete input   (1 bit, read-only, ref 1xxxx)
    DO channel -> coil             (1 bit, read/write, ref 0xxxx)
    AI channel -> input register   (word(s), read-only, ref 3xxxx)
    AO channel -> holding register (word(s), read/write, ref 4xxxx)

This module lets a device declare ``io_modules`` instead of (or as well as) raw
``registers``, and lets the lab wire **emulated** sensors/actuators to module
channels — no physical device needed. Both are front-ends only: :func:`expand`
lowers them to the existing register model (channel -> register) and adapter
layer (sensor -> simulated adapter + ``io_map`` entry), so the device runtime,
the MTU, validation and the adapter host run unchanged.

    "devices": [{"id": "PLC1", "ip": "10.0.1.1",
                 "io_modules": [{"type": "AI", "channels": 4},
                                {"type": "DO", "channels": 4}]}],
    "sensors":   [{"id": "TT101", "type": "temperature",
                   "device": "PLC1", "channel": "AI0"}],
    "actuators": [{"id": "FAN1", "type": "fan",
                   "device": "PLC1", "channel": "DO0"}]

Channels are named per type across the whole device, in module order
(``AI0, AI1, …``, ``DO0, …``) — the flat style of common remote-I/O products —
and are packed into their Modbus table after any explicit registers.
"""

from __future__ import annotations

import copy

from . import fieldbus as fb

#: module type -> the Modbus area its channels live in, default data type, and
#: which writer owns the channel (a field device produces inputs; the MTU
#: commands outputs).
MODULES = {
    "DI": {"area": "discrete_input", "data_type": "bool", "owner": "process",
           "direction": "in", "label": "Digital input"},
    "DO": {"area": "coil", "data_type": "bool", "owner": "mtu",
           "direction": "out", "label": "Digital output"},
    "AI": {"area": "input_register", "data_type": "float32", "owner": "process",
           "direction": "in", "label": "Analog input"},
    "AO": {"area": "holding_register", "data_type": "float32", "owner": "mtu",
           "direction": "out", "label": "Analog output"},
}
MAX_CHANNELS = 16

#: emulated sensors: they only produce numbers (not physical truth) from a
#: waveform; each kind fits one module type. Defaults are overridable per sensor.
SENSORS = {
    "temperature": {"module": "AI", "unit": "°C", "wave": "sine", "min": 18.0, "max": 30.0, "period": 60.0},
    "humidity":    {"module": "AI", "unit": "%RH", "wave": "sine", "min": 35.0, "max": 65.0, "period": 90.0},
    "pressure":    {"module": "AI", "unit": "bar", "wave": "sine", "min": 1.0, "max": 6.0, "period": 45.0},
    "level":       {"module": "AI", "unit": "m", "wave": "ramp", "min": 0.0, "max": 5.0, "period": 120.0},
    "flow":        {"module": "AI", "unit": "m3/h", "wave": "random", "min": 10.0, "max": 50.0},
    "analog":      {"module": "AI", "unit": "", "wave": "constant", "value": 0.0},
    "switch":      {"module": "DI", "unit": "", "wave": "square", "min": 0.0, "max": 1.0, "period": 20.0, "duty": 0.5},
}
#: emulated actuators: they record the command the MTU writes to their channel.
ACTUATORS = {
    "pump": {"module": "DO", "unit": ""}, "fan": {"module": "DO", "unit": ""},
    "valve": {"module": "DO", "unit": ""}, "lamp": {"module": "DO", "unit": ""},
    "motor": {"module": "DO", "unit": ""},
    "valve_position": {"module": "AO", "unit": "%"}, "speed": {"module": "AO", "unit": "%"},
}
WAVES = ("constant", "ramp", "sine", "random", "square")
_WAVE_KEYS = ("wave", "min", "max", "value", "period", "duty")


def catalog() -> dict:
    """Module and field-device types, for a UI to offer as choices."""
    return {"modules": MODULES, "sensors": SENSORS, "actuators": ACTUATORS,
            "waves": list(WAVES), "max_channels": MAX_CHANNELS}


# ---- channels ---------------------------------------------------------------
def channels(device: dict) -> list[dict]:
    """The device's I/O channels, in module order: ``{id, type, slot, index,
    area, data_type, owner, direction}``. Unknown module types are skipped
    (:func:`validate` reports them)."""
    count: dict = {}
    out = []
    for slot, m in enumerate(device.get("io_modules") or [], start=1):
        t = m.get("type")
        spec = MODULES.get(t)
        if spec is None:
            continue
        try:
            n = int(m.get("channels", 0))
        except (TypeError, ValueError):
            continue
        dtype = spec["data_type"] if spec["data_type"] == "bool" \
            else m.get("data_type", spec["data_type"])
        for k in range(max(0, min(n, MAX_CHANNELS))):
            i = count.get(t, 0)
            count[t] = i + 1
            ch = {"id": f"{t}{i}", "type": t, "slot": m.get("slot", slot), "index": k,
                  "area": spec["area"], "data_type": dtype, "owner": spec["owner"],
                  "direction": spec["direction"]}
            for key in ("scale", "offset"):
                if key in m and dtype != "bool":
                    ch[key] = m[key]
            out.append(ch)
    return out


def channel_registers(device: dict) -> list[dict]:
    """Register declarations for the device's channels (unpinned: packed after
    any explicit registers)."""
    regs = []
    for ch in channels(device):
        r = {"id": ch["id"], "area": ch["area"], "data_type": ch["data_type"],
             "owner": ch["owner"]}
        for key in ("scale", "offset"):
            if key in ch:
                r[key] = ch[key]
        regs.append(r)
    return regs


def _sensor_spec(s: dict) -> dict:
    """The waveform for a sensor: its type's defaults, overridden per sensor."""
    spec = {k: v for k, v in SENSORS.get(s.get("type"), {}).items() if k in _WAVE_KEYS}
    spec.update({k: s[k] for k in _WAVE_KEYS if k in s})
    return spec


def _wired(cfg: dict):
    """Yield ``(kind, entry)`` for every field device: sensors then actuators."""
    for s in cfg.get("sensors") or []:
        yield "sensor", s
    for a in cfg.get("actuators") or []:
        yield "actuator", a


# ---- lowering to registers + adapters ---------------------------------------
def expand(cfg: dict) -> dict:
    """Lower ``io_modules`` / ``sensors`` / ``actuators`` to the register model
    and the adapter layer. Returns a new config (the input is not modified);
    a config that uses none of them is returned as-is. Entries that would not
    validate are skipped — :func:`validate` reports them."""
    devices = cfg.get("devices") or []
    if not any(d.get("io_modules") for d in devices) \
            and not cfg.get("sensors") and not cfg.get("actuators"):
        return cfg
    x = copy.deepcopy(cfg)
    chans = {}
    for d in x.get("devices") or []:
        extra = channel_registers(d)
        if extra:
            d["registers"] = list(d.get("registers") or []) + extra
        chans[d.get("id")] = {c["id"]: c for c in channels(d)}
    adapters = list(x.get("adapters") or [])
    io_map = list(x.get("io_map") or [])
    for kind, f in _wired(x):
        types = SENSORS if kind == "sensor" else ACTUATORS
        ch = chans.get(f.get("device"), {}).get(f.get("channel"))
        if f.get("type") not in types or ch is None or not f.get("id"):
            continue
        var = _sensor_spec(f) if kind == "sensor" else {"actuator": True}
        adapters.append({"id": f["id"], "kind": "simulated", "spec": {"value": var}})
        io_map.append({"adapter": f["id"], "external": {"var": "value"},
                       "device": f["device"], "register": f["channel"],
                       "direction": "in" if kind == "sensor" else "out",
                       "owner": "process"})
    x["adapters"], x["io_map"] = adapters, io_map
    return x


# ---- validation ---------------------------------------------------------------
def validate(cfg: dict) -> list[str]:
    """Problems with I/O modules and emulated field devices (empty == fine)."""
    out: list[str] = []
    chans = {}
    for d in cfg.get("devices") or []:
        did = d.get("id")
        mods = d.get("io_modules")
        if mods is None:
            continue
        if not isinstance(mods, list):
            out.append(f"{did}: io_modules must be a list")
            continue
        for slot, m in enumerate(mods, start=1):
            t = m.get("type") if isinstance(m, dict) else None
            if t not in MODULES:
                out.append(f"{did} slot {slot}: unknown module type {t!r} "
                           f"(one of {', '.join(MODULES)})")
                continue
            n = m.get("channels")
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_CHANNELS:
                out.append(f"{did} slot {slot}: channels must be 1..{MAX_CHANNELS}, got {n!r}")
            dt = m.get("data_type")
            if dt is not None and (t in ("DI", "DO") or dt not in fb.DATA_TYPES or dt == "bool"):
                out.append(f"{did} slot {slot}: bad data_type {dt!r} for a {t} module")
        chan = {c["id"]: c for c in channels(d)}
        clash = chan.keys() & {r.get("id") for r in d.get("registers") or []}
        for rid in sorted(clash):
            out.append(f"{did}: register {rid!r} clashes with an I/O channel name")
        chans[did] = chan

    devices = {d.get("id") for d in cfg.get("devices") or []}
    seen_ids = {a.get("id") for a in cfg.get("adapters") or []}
    used: dict = {}
    for kind, f in _wired(cfg):
        fid = f.get("id")
        types = SENSORS if kind == "sensor" else ACTUATORS
        where = f"{kind} {fid!r}"
        if not fid:
            out.append(f"{kind} without an id")
            continue
        if fid in seen_ids:
            out.append(f"{where}: duplicate id")
        seen_ids.add(fid)
        if f.get("type") not in types:
            out.append(f"{where}: unknown type {f.get('type')!r} "
                       f"(one of {', '.join(types)})")
            continue
        dev, chid = f.get("device"), f.get("channel")
        if dev not in devices:
            out.append(f"{where}: unknown device {dev!r}")
            continue
        ch = chans.get(dev, {}).get(chid)
        if ch is None:
            out.append(f"{where}: {dev} has no I/O channel {chid!r}")
            continue
        need = types[f["type"]]["module"]
        if ch["type"] != need:
            out.append(f"{where}: a {f['type']} needs a {need} channel, "
                       f"but {dev}.{chid} is {ch['type']}")
        if (dev, chid) in used:
            out.append(f"{where}: {dev}.{chid} is already wired to {used[(dev, chid)]!r}")
        used[(dev, chid)] = fid
        if kind == "sensor":
            spec = _sensor_spec(f)
            if spec.get("wave", "constant") not in WAVES:
                out.append(f"{where}: unknown wave {spec.get('wave')!r} (one of {', '.join(WAVES)})")
            lo, hi = spec.get("min", 0.0), spec.get("max", 1.0)
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi)):
                out.append(f"{where}: min/max must be numbers")
            elif lo > hi:
                out.append(f"{where}: min {lo} is above max {hi}")
    return out


# ---- inspection -------------------------------------------------------------
def labels(cfg: dict) -> dict:
    """``{(device, channel): "TT101 temperature (°C)"}`` for wired channels."""
    out = {}
    for kind, f in _wired(cfg):
        spec = (SENSORS if kind == "sensor" else ACTUATORS).get(f.get("type"), {})
        unit = f.get("unit", spec.get("unit", ""))
        out[(f.get("device"), f.get("channel"))] = \
            f"{f.get('id')} {f.get('type')}" + (f" ({unit})" if unit else "")
    return out


def channel_table(cfg: dict) -> dict:
    """Per device, the Modbus map of its I/O channels — what an MTU (or an
    analyst) configures against: ``{device: [{channel, type, slot, area, table,
    access, address, ref, count, data_type, wired}]}``. Tolerates an incomplete
    config (a device whose registers are broken gets an ``error`` row)."""
    lab = labels(cfg)
    out = {}
    for d in cfg.get("devices") or []:
        chans = {c["id"]: c for c in channels(d)}
        if not chans:
            continue
        node = {"id": d.get("id"),
                "registers": list(d.get("registers") or []) + channel_registers(d)}
        try:
            rows = fb.Device(d.get("id"), fb.registers_from_config(node)).register_map()
        except Exception as e:                    # broken explicit registers
            out[d.get("id")] = [{"error": str(e)}]
            continue
        table = []
        for r in rows:
            c = chans.get(r["point"])
            if c is None:
                continue
            table.append({"channel": r["point"], "type": c["type"], "slot": c["slot"],
                          "area": r["area"], "table": r["table"], "access": r["access"],
                          "address": r["address"], "ref": r["ref"], "count": r["count"],
                          "data_type": r["data_type"],
                          "wired": lab.get((d.get("id"), r["point"]))})
        out[d.get("id")] = table
    return out
