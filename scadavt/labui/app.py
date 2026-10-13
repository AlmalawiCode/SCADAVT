"""The lab API — one clean surface for the web builder and the terminal client.

Endpoints (all JSON):

    GET  /                       the drag-and-drop builder page
    POST /validate   {config}            -> {issues: [...]}
    POST /generate   {config}            -> {files: {name: text}}   (CORE session + gateway)
    POST /run        {config, cycles,     -> {traces, summary}      (one-shot preview run)
                      attacks?}
    live sessions (drive from UI polling or from the terminal / a script):
    POST   /sessions {config, cycles?}   -> {id, plcs}
    POST   /sessions/{id}/attack {kind,   -> {ok, attacks}          (schedule OR inject now)
                      target, start?, end?, duration?}
    POST   /sessions/{id}/step   {n?}     -> {t, traces, summary}
    GET    /sessions/{id}                 -> {t, traces, summary}
    POST   /sessions/{id}/stop            -> {ok}

The attack endpoints are the terminal/scripting path the researcher asked for:
a script can create a session, step it, and inject an attack mid-run.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core import coregen
from .sim import Lab

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="SCADAVT Lab API")
_sessions: dict = {}


def make_engine(cfg: dict):
    """The REAL EPANET engine when the config carries a physical design and the
    EPANET library is present; otherwise the lightweight preview."""
    if cfg.get("epanet"):
        try:
            from .wds_lab import WdsLab
            return WdsLab(cfg["epanet"]), "epanet"
        except Exception:
            pass
    return Lab(cfg), "preview"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/make")
def make(plcs: int = 3, mtu: bool = True, protocol: str = "modbus"):
    """The single source of truth for a lab config (EPANET-backed), used by the
    web builder so a run goes through the real engine with register bindings."""
    from .config import make_lab
    return make_lab(plcs, with_mtu=mtu, protocol=protocol)


@app.post("/import_inp")
def import_inp(body: dict = Body(...)):
    """Parse an uploaded EPANET .inp into a design the builder draws. The .inp is
    kept verbatim (``inp_text``) and used authoritatively when the lab runs."""
    from .inp import parse_inp, summary
    design = parse_inp(body.get("inp", ""))
    return {"design": design, "summary": summary(design)}


@app.post("/compose")
def compose(body: dict = Body(...)):
    """Build a full runnable config from a hand-drawn EPANET design + PLC element
    assignments (+ optional rules). The register bindings and auto rules are
    derived here, so the drag-and-draw UI only sends the drawing."""
    from .config import compose_lab
    rules = body.get("rules")
    if body.get("rules_text"):          # per-PLC instructions in the thesis RULES grammar
        from ..builder.rules import parse_rules_txt
        rules = parse_rules_txt(body["rules_text"])
    cfg = compose_lab(body.get("design") or {}, body.get("plcs") or [],
                      rules=rules, with_mtu=body.get("with_mtu", True),
                      protocol=body.get("protocol", "modbus"), name=body.get("name"))
    return {"config": cfg, "issues": coregen.validate(cfg)}


@app.post("/validate")
def validate(config: dict = Body(...)):
    return {"issues": coregen.validate(config)}


@app.post("/generate")
def generate(config: dict = Body(...)):
    issues = coregen.validate(config)
    if issues:
        raise HTTPException(400, {"issues": issues})
    ep, pts = coregen.gateway_files(config)
    return {"files": {
        "session.json": coregen.session(config),
        "core_session.py": coregen.core_build_script(config),
        "run_devices.sh": coregen.run_script(config),
        "gate_endpoints.ini": ep,
        "gate_points.ini": pts,
    }}


def _apply_attacks(lab, attacks: list) -> None:
    for a in attacks or []:
        lab.add_attack(a["kind"], a["target"], a.get("start", 0), a.get("end", 10 ** 9))


@app.post("/run")
def run(body: dict = Body(...)):
    cfg = body.get("config") or {}
    issues = coregen.validate(cfg)
    if issues:
        raise HTTPException(400, {"issues": issues})
    lab, engine = make_engine(cfg)
    try:
        _apply_attacks(lab, body.get("attacks"))
        lab.run(int(body.get("cycles", 120)))
        return {"traces": lab.traces(), "summary": lab.summary(), "engine": engine,
                "frames": lab.frames() if hasattr(lab, "frames") else [],
                "placement": cfg.get("control", {}).get("placement", "mtu")}
    finally:
        if hasattr(lab, "close"):
            lab.close()


@app.post("/netrun")
async def netrun(body: dict = Body(...)):
    """Full networked run: the real device nodes + gateway + external EPANET app
    + MTU control, wired over sockets (the CORE node software). Needs an EPANET
    design in the config."""
    cfg = body.get("config") or {}
    if not cfg.get("epanet"):
        raise HTTPException(400, "networked run needs an EPANET design")
    issues = coregen.validate(cfg)
    if issues:
        raise HTTPException(400, {"issues": issues})
    from .netrun import NetworkedRun
    nr = NetworkedRun(cfg)
    _apply_attacks(nr, body.get("attacks"))
    return await nr.run(int(body.get("cycles", 60)))


# ---------------------------------------------------------------- live sessions
@app.post("/sessions")
def create_session(body: dict = Body(...)):
    cfg = body.get("config") or {}
    issues = coregen.validate(cfg)
    if issues:
        raise HTTPException(400, {"issues": issues})
    sid = uuid.uuid4().hex[:12]
    lab, engine = make_engine(cfg)
    _sessions[sid] = lab
    return {"id": sid, "plcs": lab.plcs, "engine": engine}


def _get(sid: str) -> Lab:
    if sid not in _sessions:
        raise HTTPException(404, "no such session")
    return _sessions[sid]


@app.post("/sessions/{sid}/attack")
def inject_attack(sid: str, body: dict = Body(...)):
    lab = _get(sid)
    start = body.get("start")
    if start is None:                      # inject now
        start = lab.t
    end = body.get("end")
    if end is None:
        end = start + int(body.get("duration", 30))
    lab.add_attack(body["kind"], body["target"], start, end)
    return {"ok": True, "attacks": [a if isinstance(a, dict) else vars(a)
                                    for a in lab.attacks]}


@app.post("/sessions/{sid}/step")
def step_session(sid: str, body: dict = Body(default={})):
    lab = _get(sid)
    lab.run(int(body.get("n", 1)))
    return {"t": lab.t, "traces": lab.traces(), "summary": lab.summary()}


@app.get("/sessions/{sid}")
def get_session(sid: str):
    lab = _get(sid)
    return {"t": lab.t, "traces": lab.traces(), "summary": lab.summary()}


@app.post("/sessions/{sid}/stop")
def stop_session(sid: str):
    lab = _sessions.pop(sid, None)
    if lab is not None and hasattr(lab, "close"):
        lab.close()
    return {"ok": True}


# mount any extra static assets under /static too
app.mount("/static", StaticFiles(directory=STATIC), name="static")
