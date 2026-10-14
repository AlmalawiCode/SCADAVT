"""Full networked run — the real CORE node software, wired over real sockets.

Unlike the in-process ``wds_lab`` preview, this stands up the actual distributed
components and makes them talk over TCP, exactly as they would inside CORE
namespaces:

    EPANET external app  --Integration API-->  gateway
    gateway  --IOModules (back channel)-->  PLC/RTU device nodes
    MTU  --Modbus/TCP (SCADA network)-->  the same device nodes

Each cycle: the gateway moves EPANET measurements into device input registers and
applies device command outputs back to EPANET; the MTU reads the measurement
registers over Modbus, the control engine decides, and the MTU writes command
registers over Modbus; then EPANET advances one step. On a CORE host the very
same processes run inside nodes on emulated links (with netem + pcap); here they
run over loopback, which carries the identical protocol stacks.

Attacks act on the SCADA path: ``dos``/``delay`` freeze the MTU's writes to the
target device; ``integrity`` forces its command off (confirmed-malicious).
"""

from __future__ import annotations

from ..core import integration as ig
from ..core.control import ControlHost
from ..core.device import run_node
from ..core.fieldbus import ModbusClientPlugin
from ..core.gateway import Gateway
from ..protocols import wdsproto as wp


def _port(server):
    return server.sockets[0].getsockname()[1]


class NetworkedRun:
    def __init__(self, cfg: dict, step_seconds: float = 300.0):
        self.cfg = cfg
        self.step_seconds = step_seconds
        self.engine = ControlHost.from_rules(cfg.get("rules", []), "mtu")
        self.t = 0
        self.attacks = []
        self.trace = []
        self.actuator_log = []       # three-way: commanded / received / applied, per actuator per cycle
        # element ownership + tank-per-PLC (for the ground-truth trace)
        self.plcs = [p["id"] for p in cfg["epanet"].get("plcs", [])]
        self.owner = {e: p["id"] for p in cfg["epanet"].get("plcs", [])
                      for e in p["elements"]}

    def add_attack(self, kind, target, start, end):
        if target not in self.plcs:
            raise ValueError(f"unknown target {target!r}")
        self.attacks.append({"kind": kind, "target": target,
                             "start": int(start), "end": int(end)})

    def _active(self, plc):
        for a in self.attacks:
            if a["target"] == plc and a["start"] <= self.t < a["end"]:
                return a
        return None

    async def run(self, cycles: int) -> dict:
        from ..examples.design_app import DesignApp   # lazy: keep core free of examples
        app = DesignApp(self.cfg["epanet"], self.cfg["mapping"])
        app_server = await app.serve()
        app_client = ig.IntegrationClient("127.0.0.1", _port(app_server))
        await app_client.hello()

        servers, devices, mtu = [], {}, {}
        session_nodes = []
        for d in self.cfg["devices"]:
            node = {"id": d["id"], "protocol": d.get("protocol", "modbus"),
                    "points": d["points"]}
            dev, fb_srv, io_srv = await run_node(node, ("127.0.0.1", 0), ("127.0.0.1", 0))
            devices[d["id"]] = dev
            servers += [fb_srv, io_srv]
            session_nodes.append({"id": d["id"], "backchannel": f"127.0.0.1:{_port(io_srv)}",
                                  "points": d["points"]})
            mtu[d["id"]] = ModbusClientPlugin("127.0.0.1", _port(fb_srv))

        gw = Gateway.from_session({"nodes": session_nodes, "mapping": self.cfg["mapping"]},
                                  app_client)

        # measurement reads (element<-register) and command writes (element->register)
        _APPLIED = {"status": (wp.PT_LINK, wp.DT_STATUS), "setting": (wp.PT_LINK, wp.DT_SETTING),
                    "speed": (wp.PT_LINK, wp.DT_SETTING), "flow": (wp.PT_LINK, wp.DT_FLOW)}
        meas, cmd_of, applied_of = [], {}, {}
        for m in self.cfg["mapping"]:
            dev = devices[m["device"]]
            if m.get("role") == ig.COMMAND:
                cmd_of[m["element"]] = (m["device"], dev.by_id[m["point"]])
                # where the command physically lands in EPANET (what was actually applied)
                applied_of[m["element"]] = _APPLIED.get(m.get("quantity", "status"),
                                                         (wp.PT_LINK, wp.DT_STATUS))
            else:
                meas.append((m["element"], m["device"], dev.by_id[m["point"]]))

        # guard: EPANET must not internally control a SCADA actuator (it would fight
        # the PLC every hydraulic step). Report any conflict with the run.
        from .inp import scada_control_conflicts
        conflicts = scada_control_conflicts(self.cfg["epanet"].get("inp_text", ""),
                                            list(applied_of))
        try:
            for _ in range(cycles):
                await self._cycle(app, gw, devices, mtu, meas, cmd_of, applied_of)
            return {"traces": self.traces(app), "summary": self.summary(),
                    "actuators": self.actuator_log, "control_conflicts": conflicts,
                    "engine": "networked"}
        finally:
            for c in mtu.values():
                await c.close()
            await gw.close()
            for s in servers:
                s.close()
            app_server.close()
            app.close()

    async def _cycle(self, app, gw, devices, mtu, meas, cmd_of, applied_of):
        rec = {"t": self.t, "attack_window": False, "malicious_confirmed": False}
        rec["attack_window"] = any(self._active(p) is not None for p in self.plcs)
        # 1) gateway: EPANET measurements -> device inputs; device commands -> EPANET
        await gw.run_cycle()
        # 2) MTU reads measurement registers over Modbus
        values = {}
        for element, dev_id, point in meas:
            try:
                values[element] = await mtu[dev_id].read(point)
            except Exception:
                pass
        read = lambda k, c, a: values.get(c)
        # 3) decide and write command registers over Modbus (attacks act here)
        commanded, attacked = {}, {}
        for c in self.engine.decide(read, self.plant_hours(app)):
            dev_id, point = cmd_of.get(c["comp"], (None, None))
            if dev_id is None:
                continue
            commanded[c["comp"]] = c["value"]            # what control DECIDED (pre-tamper)
            atk = self._active(self.owner.get(c["comp"]))
            attacked[c["comp"]] = atk["kind"] if atk else None
            if atk and atk["kind"] in ("dos", "delay"):
                continue                                 # command never reaches the PLC register
            value = c["value"]
            if atk and atk["kind"] == "integrity":
                value = 0.0                              # tampered on the SCADA network
                rec["malicious_confirmed"] = True
            try:
                await mtu[dev_id].write(point, value)
            except Exception:
                pass
        # 4) push fresh commands to EPANET, then advance the hydraulics via the API
        await gw.run_cycle()
        await gw.app.step(self.step_seconds)
        self.t += 1
        # three-way actuator record: commanded (decision) vs received (PLC register) vs
        # applied (the value EPANET actually used) — the evidence for attack studies
        for comp, (dev_id, point) in cmd_of.items():
            recv = devices[dev_id].get_output(point.id)  # what the PLC register now holds
            pt, dt = applied_of.get(comp, (wp.PT_LINK, wp.DT_STATUS))
            try:
                appl = round(app.plant.net.get(comp, pt, dt), 4)
            except Exception:
                appl = None
            self.actuator_log.append({
                "t": self.t, "actuator": comp,
                "commanded": commanded.get(comp),
                "received": round(recv, 4) if recv is not None else None,
                "applied": appl,
                "attack": attacked.get(comp)})
        for plc in self.plcs:
            tank = self._tank_of(app, plc)
            if tank is not None:
                rec[f"{plc}_level"] = round(app.plant.net.get(tank, wp.PT_NODE, wp.DT_LEVEL), 3)
            rec[f"{plc}_attack"] = self._active(plc) is not None
        self.trace.append(rec)

    def plant_hours(self, app):
        return app.plant.net.t / 3600.0

    def _tank_of(self, app, plc):
        for e, owner in self.owner.items():
            if owner == plc and e in app.plant.net.tanks:
                return e
        return None

    def traces(self, app):
        out = {}
        for plc in self.plcs:
            if self._tank_of(app, plc) is not None:
                out[plc] = [r.get(f"{plc}_level") for r in self.trace]
        return out

    def summary(self):
        win = sum(r["attack_window"] for r in self.trace)
        mal = sum(r["malicious_confirmed"] for r in self.trace)
        return {"engine": "networked", "plcs": len(self.plcs), "cycles": len(self.trace),
                "attack_window_cycles": win, "confirmed_malicious_cycles": mal,
                "benign_in_window": win - mal, "attacks": list(self.attacks)}
