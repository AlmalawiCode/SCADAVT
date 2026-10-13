"""Launch the research-lab web server.

    python -m scadavt.webui                 # http://127.0.0.1:8000
    python -m scadavt.webui --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args(argv)

    import uvicorn
    uvicorn.run("scadavt.webui.app:app", host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
