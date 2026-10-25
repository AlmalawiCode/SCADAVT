"""`python -m scadavt.service` — run the thin HTTP service with uvicorn.

Local-by-default and auth-gated for remote (R10):

    python -m scadavt.service                       # 127.0.0.1:8099, open locally
    python -m scadavt.service --host 0.0.0.0 --token SECRET   # remote: token required
    SCADAVT_TOKEN=SECRET python -m scadavt.service --host 0.0.0.0

A non-local bind without a token is refused. If no token is given for a non-local
bind we do not silently expose the service — create_app raises.
"""

from __future__ import annotations

import argparse
import os


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scadavt.service", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--token", default=os.environ.get("SCADAVT_TOKEN"))
    ap.add_argument("--cors-origin", action="append", default=None,
                    help="an extra browser origin allowed to POST (repeatable)")
    a = ap.parse_args(argv)

    from .app import create_app, _is_local
    token = a.token
    if not _is_local(a.host) and not token:
        # generate one rather than expose unauthenticated; print it so the
        # operator can use it (stderr, once).
        import secrets
        token = secrets.token_urlsafe(24)
        print(f"[scadavt.service] non-local bind: generated auth token = {token}",
              flush=True)

    app = create_app(token=token, bind_host=a.host, cors_origins=a.cors_origin)
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
