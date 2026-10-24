"""The framework CLI — ``scadavt <verb>`` (Phase 2, Milestone 3).

A domain-neutral, Python-first command line over the shared model (L0), the
single validator (L1) and the execution engine (L2). Both entry points —
``python -m scadavt`` and the ``scadavt`` console-script — call this module's
:func:`main`, so there is exactly one implementation behind them (R11).

The CLI depends only on L0–L2; it imports no GUI and no HTTP service (R5). The
live-execution verbs (:func:`cmd_run`, :func:`cmd_plan`) import the CORE engine
(`scadavt.core.corelive`) **lazily**, so `validate`/`build`/`scaffold`/`export`
work on any host even when the CORE libraries are absent, and the CLI itself has
no hard dependency on the engine module.

Verbs::

    scadavt validate  CONFIG               # schema + semantic validation (L1)
    scadavt build      CONFIG [-o OUT]      # generate CORE artefacts (L2)
    scadavt scaffold   [--plcs N ...] [-o]  # emit a starting config (grid helper)
    scadavt export     CONFIG [--format]    # round-trip / convert (JSON<->YAML)
    scadavt plan       CONFIG               # resolved node/link plan (offline)
    scadavt run        CONFIG [--live ...]  # build + run on live CORE
"""

from __future__ import annotations

import argparse
import sys

from .core import model, validation


def _load(path: str) -> dict:
    return model.load(path)


def _print_issues(issues: list[str]) -> None:
    for i in issues:
        print(f"  - {i}")


# --------------------------------------------------------------------------
# verbs that need only L0–L2 (no CORE engine)
# --------------------------------------------------------------------------
def cmd_validate(a) -> int:
    cfg = _load(a.config)
    issues = validation.validate(cfg)
    if issues:
        print(f"INVALID ({len(issues)} issue(s)):")
        _print_issues(issues)
        return 1
    print("OK — config is valid (structure + semantics)")
    return 0


def cmd_build(a) -> int:
    from .core import coregen
    cfg = validation.ensure_valid(_load(a.config))
    out = a.out or "scadavt_out"
    files = coregen.write_session(cfg, out)
    print(f"wrote artefacts to {out}/:")
    for name in (files if isinstance(files, (list, tuple)) else files.keys()
                 if isinstance(files, dict) else []):
        print(f"  - {name}")
    return 0


def cmd_export(a) -> int:
    cfg = _load(a.config)
    if a.format == "yaml":
        import yaml
        text = yaml.safe_dump(cfg, sort_keys=True)
    else:
        text = model.dumps(cfg)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + ("\n" if not text.endswith("\n") else ""))
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


def cmd_scaffold(a) -> int:
    corelive = _import_engine(need_core=False)
    if corelive is None:
        return 3
    cfg = corelive.grid(a.plcs, per_subnet=a.per_subnet)
    # strip private helper keys the generator adds for its own tools
    cfg = {k: v for k, v in cfg.items() if not k.startswith("_")}
    cfg = model.with_version(cfg)
    text = model.dumps(cfg)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
        print(f"wrote {a.out}  ({a.plcs} PLCs)")
    else:
        print(text)
    return 0


def cmd_plan(a) -> int:
    corelive = _import_engine(need_core=False)
    if corelive is None:
        return 3
    cfg = validation.ensure_valid(_load(a.config))
    plan = corelive.build_plan(cfg)
    print(f"nodes ({len(plan['nodes'])}):")
    for n in plan["nodes"]:
        print(f"  {n['id']:<8} {n['kind']:<7} {n.get('model') or ''}")
    print(f"links ({len(plan['links'])}), routers={plan['routers']}")
    return 0


# --------------------------------------------------------------------------
# live verb (needs CORE + daemon)
# --------------------------------------------------------------------------
def cmd_run(a) -> int:
    corelive = _import_engine(need_core=True)
    if corelive is None:
        return 3
    cfg = validation.ensure_valid(_load(a.config))
    from core.api.grpc import client
    core = client.CoreGrpcClient()
    core.connect()
    net = corelive.build(core, cfg)
    try:
        ok, warn = net.start(cycles=a.cycles)
        print(f"session started ok={ok} warnings={warn}")
        # minimal liveness: MTU reaches every device
        mtu = cfg.get("mtu", {}).get("id")
        reached = 0
        for d in cfg["devices"]:
            if mtu and d.get("ip") and net.ping(mtu, d["ip"]):
                reached += 1
        print(f"{mtu} reached {reached}/{len(cfg['devices'])} devices")
        return 0 if reached == len(cfg["devices"]) else 1
    finally:
        if not a.keep:
            net.stop()


def _import_engine(need_core: bool):
    """Lazily import the CORE engine. Returns the corelive module, or None (with
    a helpful message) when it — or the CORE libraries — are unavailable."""
    try:
        from .core import corelive
    except Exception as e:
        print(f"error: the execution engine (scadavt.core.corelive) is not "
              f"available here ({e}).", file=sys.stderr)
        return None
    if need_core:
        try:
            import core.api.grpc  # noqa: F401
        except Exception as e:
            print(f"error: the CORE Python API is not importable ({e}). Run live "
                  f"verbs on a CORE host with core-daemon.", file=sys.stderr)
            return None
    return corelive


# --------------------------------------------------------------------------
# parser + single entry point (R11)
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="scadavt", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate a config (schema + semantics)")
    v.add_argument("config"); v.set_defaults(fn=cmd_validate)

    b = sub.add_parser("build", help="generate CORE artefacts from a config")
    b.add_argument("config"); b.add_argument("-o", "--out"); b.set_defaults(fn=cmd_build)

    sc = sub.add_parser("scaffold", help="emit a starting config (grid helper)")
    sc.add_argument("--plcs", type=int, default=3)
    sc.add_argument("--per-subnet", type=int, default=8)
    sc.add_argument("-o", "--out"); sc.set_defaults(fn=cmd_scaffold)

    ex = sub.add_parser("export", help="round-trip / convert a config")
    ex.add_argument("config"); ex.add_argument("--format", choices=["json", "yaml"], default="json")
    ex.add_argument("-o", "--out"); ex.set_defaults(fn=cmd_export)

    pl = sub.add_parser("plan", help="print the resolved node/link plan (offline)")
    pl.add_argument("config"); pl.set_defaults(fn=cmd_plan)

    r = sub.add_parser("run", help="build + run the config on live CORE")
    r.add_argument("config"); r.add_argument("--live", action="store_true",
                                             help="(accepted; run is always live)")
    r.add_argument("--cycles", type=int, default=60); r.add_argument("--keep", action="store_true")
    r.set_defaults(fn=cmd_run)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except validation.ValidationError as e:
        print(f"INVALID config:", file=sys.stderr)
        _print_issues(e.result.issues)
        return 1
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
