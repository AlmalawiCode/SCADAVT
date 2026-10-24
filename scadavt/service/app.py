"""The thin HTTP service (FastAPI) — wraps L1/L2, holds no business logic.

Endpoints (all stateless; the client owns the config file):

    GET  /health            liveness + whether the live engine is available
    POST /validate  {config}            -> {ok, issues}
    POST /plan      {config}            -> {nodes, links, routers}   (offline)
    POST /build     {config}            -> {files: [...]}            (writes artefacts)
    POST /run       {config, cycles?}   -> {started, reached, devices}  (live CORE)

The service wraps the *same* `scadavt.core.validation` and engine the Python
CLI uses (R2); it never re-implements validation or topology. The CORE engine is
imported lazily so the service starts on a host without CORE (only ``/run`` then
fails, with 503).

Security (R10) — see :func:`create_app`.
"""

from __future__ import annotations

import hmac
import os
import tempfile

from scadavt.core import coregen, validation

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_local(host: str) -> bool:
    return host in _LOCAL_HOSTS


def create_app(token: str | None = None, *, bind_host: str = "127.0.0.1",
               cors_origins: list[str] | None = None, serve_designer: bool = True):
    """Build the FastAPI app.

    :param token: shared secret required in the ``X-SCADAVT-Token`` header for
        every state-changing (POST) request. When ``bind_host`` is **non-local**
        a token is mandatory — ``create_app`` raises ``ValueError`` without one,
        so the service is never exposed to the network unauthenticated.
    :param bind_host: the host the server will bind to (used only to decide
        whether a token is mandatory; the actual bind happens in ``__main__``).
    :param cors_origins: extra browser origins allowed to make state-changing
        requests. By default only same-origin local pages are allowed, which —
        together with the custom token header — stops a drive-by page in a local
        browser from driving the service (R10).
    """
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    if not _is_local(bind_host) and not token:
        raise ValueError("a non-local bind requires an auth token "
                         "(set token=... / SCADAVT_TOKEN) — refusing to expose "
                         "the service unauthenticated")

    allowed_origins = set(cors_origins or [])

    app = FastAPI(title="SCADAVT service", version="1")
    app.state.token = token
    app.state.allowed_origins = allowed_origins

    @app.middleware("http")
    async def _guard(request: Request, call_next):
        # read-only GETs stay open and convenient on the local bind
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        # --- state-changing request protection (R10) ---
        # 1) token, if configured (constant-time compare)
        if app.state.token:
            supplied = request.headers.get("x-scadavt-token", "")
            if not hmac.compare_digest(supplied, app.state.token):
                return JSONResponse({"detail": "missing or invalid auth token"},
                                    status_code=401)
        # 2) anti-CSRF: a cross-origin browser request carries an Origin header;
        #    reject it unless it is same-origin (the served designer) or
        #    explicitly allow-listed. Non-browser clients (curl/httpx/the CLI)
        #    send no Origin and pass.
        origin = request.headers.get("origin")
        if origin is not None:
            host = request.headers.get("host", "")
            same_origin = origin in (f"http://{host}", f"https://{host}")
            if not same_origin and origin not in app.state.allowed_origins:
                return JSONResponse(
                    {"detail": f"cross-origin state-changing request from {origin!r} "
                               f"is not allowed"}, status_code=403)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"status": "ok", "engine_available": _engine_available()}

    @app.post("/validate")
    async def validate(body: dict):
        cfg = body.get("config", body)
        r = validation.check(cfg)
        return {"ok": r.ok, "issues": r.issues}

    @app.post("/plan")
    async def plan(body: dict):
        cfg = body.get("config", body)
        r = validation.check(cfg)
        if not r.ok:
            return JSONResponse({"detail": "invalid config", "issues": r.issues},
                                status_code=422)
        corelive = _lazy_engine()
        if corelive is None:
            return JSONResponse({"detail": "engine module unavailable"}, status_code=503)
        p = corelive.build_plan(cfg)
        return {"nodes": p["nodes"], "links": p["links"], "routers": p["routers"]}

    @app.post("/build")
    async def build(body: dict):
        cfg = body.get("config", body)
        r = validation.check(cfg)
        if not r.ok:
            return JSONResponse({"detail": "invalid config", "issues": r.issues},
                                status_code=422)
        out = tempfile.mkdtemp(prefix="scadavt_svc_")
        files = coregen.write_session(cfg, out)
        names = list(files.keys()) if isinstance(files, dict) else list(files or [])
        return {"out": out, "files": names}

    @app.post("/run")
    async def run(body: dict):
        cfg = body.get("config", body)
        cycles = int(body.get("cycles", 60))
        r = validation.check(cfg)
        if not r.ok:
            return JSONResponse({"detail": "invalid config", "issues": r.issues},
                                status_code=422)
        try:
            return _execute(cfg, cycles)
        except _EngineUnavailable as e:
            return JSONResponse({"detail": str(e)}, status_code=503)

    # serve the generic designer (static, same-origin) at /designer
    if serve_designer:
        import os
        from fastapi.staticfiles import StaticFiles
        static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "designer", "static")
        if os.path.isdir(static_dir):
            app.mount("/designer", StaticFiles(directory=static_dir, html=True),
                      name="designer")

    return app


class _EngineUnavailable(RuntimeError):
    pass


def _execute(cfg: dict, cycles: int) -> dict:
    """Run a config live and return the actual results via the ONE shared runner
    (:func:`scadavt.core.corelive.run_live`). Two deployments, same runner and
    same result shape:

    * **in-process** — when this process can import the CORE Python API
      (``pip install .[webui]`` in the CORE environment), run directly;
    * **delegated** — when ``SCADAVT_CORE_PYTHON`` names a CORE-capable
      interpreter, run ``<it> -m scadavt run <cfg> --live --json`` as a
      subprocess (it connects out to the daemon). This lets the HTTP service run
      on an interpreter that cannot itself load CORE.
    """
    import os
    core_py = os.environ.get("SCADAVT_CORE_PYTHON")
    if core_py:
        import json
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(cfg, fh)
            cfgpath = fh.name
        try:
            out = subprocess.run([core_py, "-m", "scadavt", "run", cfgpath,
                                  "--live", "--json", "--cycles", str(cycles)],
                                 capture_output=True, text=True, timeout=600,
                                 env=dict(os.environ))
        finally:
            os.unlink(cfgpath)
        lines = [l for l in out.stdout.splitlines() if l.strip().startswith("{")]
        if not lines:
            raise _EngineUnavailable(f"delegated run failed: {out.stderr.strip()[-400:]}")
        return json.loads(lines[-1])
    corelive = _lazy_engine()
    if corelive is None or not _engine_available():
        raise _EngineUnavailable("live CORE engine not available on this host "
                                 "(and SCADAVT_CORE_PYTHON is not set)")
    from core.api.grpc import client
    core = client.CoreGrpcClient()
    core.connect()
    return corelive.run_live(core, cfg, cycles=cycles)


def _lazy_engine():
    try:
        from scadavt.core import corelive
        return corelive
    except Exception:
        return None


def _engine_available() -> bool:
    try:
        import core.api.grpc  # noqa: F401
        return True
    except Exception:
        return False
