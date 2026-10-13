"""SCADAVT lab terminal client.

Drives the same lab as the web builder, from the command line or a script.
Local mode needs no server; the ``session`` commands talk to a running API
(``scadavt.labui.app``) so you can inject an attack mid-run from the terminal.

    # build + inspect a lab
    python -m scadavt.labui.cli make --plcs 4 --no-mtu -o lab.json
    python -m scadavt.labui.cli validate lab.json
    python -m scadavt.labui.cli generate lab.json -o out/

    # run locally, scheduling attacks (kind:target:start:end)
    python -m scadavt.labui.cli run lab.json --cycles 120 \
        --attack dos:PLC1:30:70 --attack integrity:PLC2:50:90 --out trace.csv

    # serve the web builder + API
    python -m scadavt.labui.cli serve                 # http://127.0.0.1:8000

    # live: create a session on a running server, step it, inject an attack
    python -m scadavt.labui.cli session new lab.json
    python -m scadavt.labui.cli session attack <id> --kind dos --target PLC1
    python -m scadavt.labui.cli session step <id> --n 40
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from ..core import coregen
from .config import make_lab
from .sim import Lab


def _load(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import yaml
        return yaml.safe_load(text)


def _parse_attacks(specs):
    out = []
    for s in specs or []:
        parts = s.split(":")
        if len(parts) != 4:
            raise SystemExit(f"bad --attack {s!r}; use kind:target:start:end")
        out.append({"kind": parts[0], "target": parts[1],
                    "start": int(parts[2]), "end": int(parts[3])})
    return out


# ------------------------------------------------------------------ local cmds
def cmd_make(a):
    cfg = make_lab(a.plcs, with_mtu=not a.no_mtu, protocol=a.proto)
    out = json.dumps(cfg, indent=2, ensure_ascii=False)
    (Path(a.out).write_text(out) if a.out else print(out))
    if a.out:
        print(f"wrote {a.out}  ({a.plcs} PLC, {'no MTU' if a.no_mtu else 'MTU'})")


def cmd_validate(a):
    issues = coregen.validate(_load(a.file))
    print("OK" if not issues else "ISSUES:\n  " + "\n  ".join(issues))
    raise SystemExit(1 if issues else 0)


def cmd_generate(a):
    cfg = _load(a.file)
    files = coregen.write_session(cfg, a.out)
    print("wrote:\n  " + "\n  ".join(sorted(files.values())))


def cmd_run(a):
    cfg = _load(a.file)
    issues = coregen.validate(cfg)
    if issues:
        raise SystemExit("invalid config:\n  " + "\n  ".join(issues))
    lab = Lab(cfg)
    for atk in _parse_attacks(a.attack):
        lab.add_attack(atk["kind"], atk["target"], atk["start"], atk["end"])
    lab.run(a.cycles)
    print(json.dumps(lab.summary(), indent=2))
    if a.out:
        with open(a.out, "w", newline="") as fh:
            w = csv.writer(fh)
            cols = list(lab.trace[0].keys())
            w.writerow(cols)
            for r in lab.trace:
                w.writerow([r[c] for c in cols])
        print(f"trace -> {a.out}")


def cmd_serve(a):
    import uvicorn
    print(f"lab builder on http://{a.host}:{a.port}")
    uvicorn.run("scadavt.labui.app:app", host=a.host, port=a.port)


# ------------------------------------------------------------------ remote cmds
def _client(url):
    import httpx
    return httpx.Client(base_url=url, timeout=10.0)


def cmd_session(a):
    with _client(a.url) as c:
        if a.sub == "new":
            r = c.post("/sessions", json={"config": _load(a.file)}).json()
            print(json.dumps(r, indent=2))
        elif a.sub == "attack":
            body = {"kind": a.kind, "target": a.target}
            if a.start is not None:
                body["start"] = a.start
            if a.duration is not None:
                body["duration"] = a.duration
            print(json.dumps(c.post(f"/sessions/{a.id}/attack", json=body).json(), indent=2))
        elif a.sub == "step":
            r = c.post(f"/sessions/{a.id}/step", json={"n": a.n}).json()
            print(json.dumps(r["summary"], indent=2))
        elif a.sub == "show":
            print(json.dumps(c.get(f"/sessions/{a.id}").json()["summary"], indent=2))
        elif a.sub == "stop":
            print(json.dumps(c.post(f"/sessions/{a.id}/stop").json(), indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="scadavt-lab", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make"); m.add_argument("--plcs", type=int, default=3)
    m.add_argument("--no-mtu", action="store_true"); m.add_argument("--proto", default="modbus")
    m.add_argument("-o", "--out"); m.set_defaults(fn=cmd_make)

    v = sub.add_parser("validate"); v.add_argument("file"); v.set_defaults(fn=cmd_validate)

    g = sub.add_parser("generate"); g.add_argument("file")
    g.add_argument("-o", "--out", default="lab_out"); g.set_defaults(fn=cmd_generate)

    r = sub.add_parser("run"); r.add_argument("file")
    r.add_argument("--cycles", type=int, default=120)
    r.add_argument("--attack", action="append", help="kind:target:start:end")
    r.add_argument("-o", "--out"); r.set_defaults(fn=cmd_run)

    s = sub.add_parser("serve"); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000); s.set_defaults(fn=cmd_serve)

    se = sub.add_parser("session"); se.add_argument("--url", default="http://127.0.0.1:8000")
    sesub = se.add_subparsers(dest="sub", required=True)
    sn = sesub.add_parser("new"); sn.add_argument("file")
    sa = sesub.add_parser("attack"); sa.add_argument("id")
    sa.add_argument("--kind", required=True); sa.add_argument("--target", required=True)
    sa.add_argument("--start", type=int); sa.add_argument("--duration", type=int)
    ss = sesub.add_parser("step"); ss.add_argument("id"); ss.add_argument("--n", type=int, default=20)
    sh = sesub.add_parser("show"); sh.add_argument("id")
    st = sesub.add_parser("stop"); st.add_argument("id")
    se.set_defaults(fn=cmd_session)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
