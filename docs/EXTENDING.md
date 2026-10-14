# Adding an application to SCADAVT (without modifying the framework)

SCADAVT is a **generic SCADA/ICS cybersecurity framework**. A *physical process*
is an **optional application package** that plugs in through the public
Integration API. The Water Distribution System (`scadavt/apps/water/`) is the
reference application; add your own (power grid, gas, manufacturing, …) the same
way **without editing anything under the generic core**.

## What is generic (reuse it — do not fork it)
- `scadavt/core/` — Integration API, field-bus/device/master/gateway, control
  host, event-stepped timing, CORE session generator, labels, bundles.
- `scadavt/protocols/{modbus,iomodules}` — the wire protocols.
- `scadavt/builder/rules.py` — the domain-neutral control-rule engine.
- `scadavt/attacks/` — the **attack infrastructure**: ARP (`arp`), transparent
  Modbus proxy (`proxy`), rewrite handlers (`handlers`: FDI/replay/command),
  MITM agent (`mitm`), command injection (`modbus_inject`), DoS (`dos`), the
  attack catalogue (`catalog`), the runner, and **dataset generation**
  (`dataset`, domain-neutral provenance). These work on Modbus frames/registers
  and are domain-independent.

## What an application package provides
Create `scadavt/apps/<name>/` (or a separate installable package that imports
`scadavt`) with:

1. **A physical model** — your simulator (any engine). Water uses EPANET in
   `apps/water/wds/`.
2. **An external process application** implementing the public Integration API
   (`scadavt.core.integration.ExternalApp`): answer `ACQUIRE` (sensor →
   SCADAVT), `CONTROL` (command → process), `CLOCK`/`STEP`. It **listens**;
   SCADAVT's gateway connects to it. See `apps/water/app/design_app.py` and the
   runnable `app_runner.py` / the gateway loop `scadavt.core.gateway_run`.
3. **Scenarios** — the network/model files plus a lab config (devices, register
   mapping, control rules). Water keeps these in `apps/water/scenarios/` and
   builds configs with `apps/water/labui/config.py`.
4. **(Optional) attack scenarios** — thin definitions that point the *generic*
   attack infrastructure at your domain targets and state the expected physical
   consequence. An attack scenario is "which register / sensor, what value, what
   should happen" — the ARP/proxy/handler/injection/DoS machinery is reused
   unchanged. (Water's live experiments: `tools/live_core_*`.)

## The contract (minimal)
- Implement `ExternalApp` (points(), acquire(), control(), clock(), step()).
- Provide a lab config dict with `devices` (points + roles), `mapping`
  (point ↔ element ↔ role), optional `rules`, and your model/scenario files.
- Hand that config to the generic pieces: `coregen` (CORE session + register
  map), `gateway_run` (bridge + clock), `master` (control), `device` (field
  node). Nothing in `scadavt/core`, `scadavt/protocols`, `scadavt/builder/rules`
  or `scadavt/attacks` needs to change.

## Keep the boundary clean
- The framework must not import your app. Domain types/logic live only in your
  app package. (Water-specific types are in `apps/water/wdsproto`, EPANET file
  generation in `apps/water/generate` — not in the core.)
- If a generic component currently has a domain-flavoured implementation,
  generalise it rather than moving it into an app (e.g. `attacks/dataset`
  records a neutral `model_file`/`model_sha256`, not an EPANET-specific field).

## Backward compatibility
Code that moved into `apps/water/` left shims at the old import paths
(`scadavt.wds`, `scadavt.labui`, `scadavt.protocols.wdsproto`,
`scadavt.builder.generate`, `scadavt.examples.*`) and a symlink for
`scadavt/scenarios`, so existing imports, `python -m` runners and scenario paths
keep working unchanged.
