"""FastAPI backend for the SCADAVT research lab.

The backend is a thin control plane over the existing, validated Python API:
it builds the topology, starts/stops process-level live runs, and streams
ground-truth frames to the browser. It never reimplements the simulation.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from fastapi.responses import PlainTextResponse

from .. import scenario as sc
from . import authoring, topology
from .live import ROOT, manager

STATIC = Path(__file__).resolve().parent / "static"
EXPERIMENTS = ROOT / "experiments"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    if manager.proc is not None:
        await manager.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="SCADAVT Research Lab", version="0.1", lifespan=_lifespan)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "scadavt-lab"}

    @app.get("/api/scenario")
    async def scenario(path: str | None = None):
        try:
            return topology.build(path)
        except Exception as e:  # malformed scenario -> readable error, not 500 HTML
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.get("/api/experiments")
    async def experiments():
        out = []
        if EXPERIMENTS.is_dir():
            for f in sorted(EXPERIMENTS.glob("*.yaml")):
                try:
                    doc = yaml.safe_load(f.read_text()) or {}
                except Exception:
                    continue
                out.append({
                    "file": f.name,
                    "name": doc.get("name", f.stem),
                    "deployment": doc.get("deployment", {}),
                    "n_faults": len(doc.get("faults", [])),
                })
        return {"experiments": out}

    @app.get("/api/run")
    async def run_status():
        return manager.status()

    @app.post("/api/run")
    async def run_start(params: dict):
        try:
            return await manager.start(params or {})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=409)

    @app.post("/api/stop")
    async def run_stop():
        return await manager.stop()

    # ----------------------------------------------------- authoring (P4)
    @app.post("/api/workspace")
    async def workspace():
        path = authoring.ensure_workspace()
        return {"path": str(path)}

    @app.post("/api/validate")
    async def validate(body: dict):
        path = body.get("path") or str(sc.DEFAULT_SCENARIO)
        return authoring.check_validity(path)

    @app.post("/api/scenario/coordinates")
    async def save_coords(body: dict):
        path = body.get("path")
        if not path:
            return JSONResponse({"error": "path required (init a workspace first)"},
                                status_code=400)
        try:
            return authoring.save_coordinates(path, body.get("coords", {}))
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/api/attacks/validate")
    async def attacks_validate(body: dict):
        path = body.get("path") or str(sc.DEFAULT_SCENARIO)
        return {"results": authoring.validate_attacks(path, body.get("attacks", []))}

    @app.post("/api/attacks/experiment")
    async def attacks_experiment(body: dict):
        name = body.get("name", "custom_attack")
        text = authoring.build_experiment_yaml(name, body.get("attacks", []),
                                               body.get("deployment"))
        return PlainTextResponse(text, headers={
            "Content-Disposition": f'attachment; filename="{name}.yaml"'})

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await ws.accept()
        manager.register(ws)
        try:
            await ws.send_json({"type": "status", **manager.status()})
            if manager.latest is not None:
                await ws.send_json({"type": "frame",
                                    "sim_h": manager.latest.get("sim_h"),
                                    "row": manager.latest})
            while True:
                await ws.receive_text()  # keep open; detect disconnect
        except WebSocketDisconnect:
            pass
        finally:
            manager.unregister(ws)

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")

    return app


app = create_app()
