# Phase 2 — Architectural Design (for approval)

**Status: DESIGN ONLY. No implementation until approved.**
Scope: how SCADAVT grows a graphical network designer *without* compromising the
Python-first engine proven in Phase 1 (PR #22, `scadavt.core.corelive`).

---

## 1. The governing requirement (Phase 2 invariant)

SCADAVT must support **two equally capable workflows**:

1. a **Python-first programmatic API/CLI**, and
2. an **optional graphical network designer (GUI)**.

Binding constraints:

- **R1 — Full programmatic capability.** Researchers and developers can create,
  configure, execute, and automate experiments **entirely in Python/CLI**, with
  the GUI never installed or running.
- **R2 — One shared model.** Both workflows use the *same* configuration model,
  the *same* validation rules, and the *same* execution engine.
- **R3 — Interchangeable configurations.** A configuration authored in Python is
  loadable and editable in the GUI, and vice-versa, losslessly.
- **R4 — GUI is a leaf.** The GUI is only a presentation and configuration
  layer. The core engine must **not** depend on it — removing the GUI entirely
  must leave the engine fully functional.

This document is the contract that makes R1–R4 structural, not aspirational.

**Binding refinements (approved).** D1–D4 (§9) are approved in principle, subject
to seven refinements that become first-class requirements R5–R11:

- **R5 — Python API/CLI independent of *both* GUI and HTTP server.** The API and
  CLI must import and run with Flask **and** the HTTP service module both absent.
- **R6 — Generic designer fully separated from water/EPANET.** LabUI is reused
  *selectively*; the generic network designer imports nothing under
  `scadavt.apps.water` and nothing EPANET-specific.
- **R7 — One model; two-stage validation.** JSON Schema defines *structure*;
  `coregen.validate` remains the **authoritative semantic validator**. Schema is
  a precondition, not a substitute.
- **R8 — Lossless round-trip of meaning.** Python↔GUI round-trip must preserve
  **topology** (nodes + links), **control relationships** (mapping + rules), and
  **every supported configuration property** — nothing silently dropped.
- **R9 — Arbitrary topologies.** The model, validator, engine, and designer must
  accept any user-defined topology; `grid()` is one convenience generator, never
  a constraint on shape.
- **R10 — HTTP service optional, local-by-default, secured for remote.** Default
  bind `127.0.0.1`; remote exposure requires explicit opt-in **and**
  authentication; the engine runs fully with the service absent.
- **R11 — One implementation behind both CLI entry points.** `python -m scadavt`
  and the `scadavt` console-script dispatch to the *same* function; no forked
  logic.

**North star:** a domain-independent, Python-first SCADA experimentation
framework with an *optional* graphical interface.

---

## 2. Where we already stand (Phase 1 baseline)

The architecture already leans the right way — Phase 2 formalises and guards it,
it does not rebuild it.

| Concern | Today | Verified |
|---|---|---|
| Config model | a plain dict (`devices` / `mtu` / `switches` / `routers` / `links` / `mapping` / `rules` / `control`), serialisable as JSON or YAML | used by every live tool |
| Validation | `scadavt.core.coregen.validate(cfg)` — single function | one code path |
| Execution engine | `scadavt.core.corelive` (+ `coregen` generators) — headless, driven from the config | live 3/12/manual PLCs, PR #22 |
| Dependency direction | `scadavt/core/*` imports **no** Flask / GUI / `labui` | `grep` clean (verified) |

**The gap:** the current CLI and the existing (water) web UI live under
`scadavt/apps/water/labui/` — i.e. the only "designer" today is domain-coupled,
and the only CLI is an app's CLI, not a framework CLI. Phase 2 lifts the model,
the CLI, and a *generic* designer to the framework level.

---

## 3. Target architecture — strict layering

Dependencies point **downward only**. No module may import a layer above it.

```
            ┌───────────────────────────────────────────────┐
  L3b  GUI  │  Graphical network designer (presentation)     │  optional, removable
            │  draw nodes/links → emits/loads a config file  │
            └───────────────┬───────────────────────────────┘
                            │  (only arrow out of the GUI)
  L3a  PY   ┌───────────────┴──────────┐  ┌────────────────────┐
            │  Python API  (corelive)  │  │  Framework CLI      │  equally capable
            │  net = build(core,cfg)   │  │  python -m scadavt  │  (R1)
            └───────────────┬──────────┘  └─────────┬──────────┘
                            └──────────────┬─────────┘
  L2  ENGINE ┌──────────────────────────────┴─────────────────┐
             │  Execution engine: corelive (live CORE) +       │  headless (R4)
             │  coregen generators (session/script/registers)  │
             └──────────────────────────────┬─────────────────┘
  L1  VALID  ┌──────────────────────────────┴─────────────────┐
             │  Validation + normalisation: coregen.validate    │  single source (R2)
             └──────────────────────────────┬─────────────────┘
  L0  MODEL  ┌──────────────────────────────┴─────────────────┐
             │  Canonical config model + schema + (de)serialise │  one contract (R2,R3)
             └─────────────────────────────────────────────────┘
```

Key rules:

- **L0–L2 never import L3.** The engine does not know a GUI exists. (This is the
  machine-checkable form of R4 — see §7.)
- **The HTTP service is itself L3**, a sibling of the GUI and the CLI — a thin
  wrapper over L1/L2 with no business logic. The **Python API and CLI never
  import it** (R5): the service depends on the engine, never the reverse.
- **Both L3a and L3b sit on the same L0–L2.** The GUI has no private model,
  validator, or engine; it calls the same functions the Python user calls. This
  is R2, and it is what guarantees R3/R8.
- **The config file is the only hand-off** between any designer and the engine.

---

## 4. L0 — the shared configuration model

The config dict is promoted to a **named, versioned, machine-readable contract**.

- **Canonical schema.** Publish the config shape as a JSON Schema at
  `scadavt/core/schema/config.schema.json`, plus thin typed accessors
  (dataclasses or `TypedDict`) in `scadavt/core/model.py`. The schema is the
  single definition both workflows (and tests) point at.
- **Serialisation.** Canonical on-disk form is **JSON**; **YAML** accepted for
  human authoring (already supported by the loader). `load(path) -> cfg` and
  `dump(cfg, path)` live in L0 and are the only I/O both sides use.
- **Domain-neutral core, optional app overlays.** Core entities are generic:
  `device`/PLC, `point`, `link`, `switch`, `router`, `mtu`, `mapping`, `rule`.
  Domain apps (Water/EPANET) attach their specifics under a namespaced key
  (e.g. `apps.water`) that the generic engine ignores — so a water config and a
  bare generic config are the same model with an optional overlay.
- **Versioning.** A `schema_version` field; L0 owns migration of older configs.
- **Arbitrary topologies (R9).** The model expresses any set of nodes and any
  set of links between them — any number of subnets, routers, back-channels, and
  cross-PLC control relationships. There is no "grid" concept in the model;
  `grid()` and friends are L3a *helpers* that merely emit ordinary configs. A
  hand-authored non-grid config (e.g. the asymmetric `--manual` topology already
  run live in PR #22) is a first-class citizen.

This directly serves **R3/R8**: because there is exactly one model and one file
format, a Python-authored config and a GUI-authored config are the *same artefact*.

---

## 5. L1 — two-stage validation, one authority (R7)

Validation is two stages with a clear authority order:

1. **Structural (JSON Schema).** The canonical schema checks shape, types, and
   required fields. It is a *precondition* — a fast, declarative gate the GUI can
   also use for live field feedback.
2. **Semantic (authoritative).** `coregen.validate(cfg) -> list[issue]` remains
   the **only** authoritative validator: duplicate IPs, unknown link endpoints,
   mapping references to non-existent points, placement rules, addressing. A
   config is valid **iff `coregen.validate` returns no issues**; passing the
   schema alone is never sufficient.

- There is no second, GUI-side validator. The GUI calls the same L1 (per
  keystroke / on save) and renders the issues L1 returns.
- Normalisation (defaulting, IP/mask inference, element↔register mapping) lives
  here so both workflows see identical resolved configs.

Serves **R2/R7**: identical rules, identical results, regardless of entry point;
schema structures, `coregen.validate` decides.

---

## 6. L3 — the two workflows

### 6a. Python-first API + CLI (the primary, always-present workflow — R1)

- **Library API:** `scadavt.core.corelive` (Phase-1) — `build(core, cfg)`,
  `LiveNetwork.start/ping/probe/stop`, plus `grid()`/builder helpers for
  assembling large configs in code. Offline-testable plan via `build_plan`.
- **Framework CLI** (new, lifted out of `apps/water`): `python -m scadavt …`
  ```
  scadavt validate  config.json              # L1 (schema + coregen.validate)
  scadavt build      config.json -o out/      # L0→L2 generate artefacts
  scadavt run        config.json [--live]     # L2 run on CORE, print results
  scadavt scaffold   --plcs 12 --per-subnet 6 # emit a starting config (grid helper)
  scadavt export     config.json --format …   # round-trip / convert
  ```
- **One implementation, two entry points (R11).** `python -m scadavt` (via
  `scadavt/__main__.py`) and the `scadavt` console-script both call a single
  `scadavt.cli:main(argv)`. No behaviour may differ between them.
- **Independent of GUI and HTTP (R5).** The API and CLI import only L0–L2;
  neither imports Flask nor the HTTP service module. They run on a headless host
  with neither installed.
- **Automation:** because everything is config-in / results-out, parameter
  sweeps, batch experiments, and CI runs are ordinary Python/shell loops over
  configs. No GUI, no display, no browser.

### 6b. Graphical network designer (optional — R4)

- A thin client that lets a user **draw** nodes (PLC/MTU/router/switch) and
  links, set IPs/points/rules, and **emit the canonical config file**; and
  **load** an existing config (Python- or GUI-authored) back onto the canvas,
  including arbitrary non-grid topologies (R9).
- It contains **no** domain or validation logic: it calls L1 for validation and
  L2 to run, and renders what comes back.
- **Water/EPANET-free (R6).** The generic designer and its service import nothing
  under `scadavt.apps.water` and nothing EPANET-specific. Reuse of the existing
  `apps/water/labui` is *selective* — generic canvas/validation/run mechanics are
  lifted to a framework-level designer; water stays an optional overlay rendered
  only when a config carries an `apps.water` section.
- **Boundary = thin HTTP service (D1, approved).** A small server wraps L1/L2
  (`validate`, `build`, `run`, `load/save`) with zero business logic. It is just
  another L3 client of the engine. **Security (R10):** binds `127.0.0.1` by
  default; remote exposure requires an explicit host/flag **and** an auth token;
  the engine runs fully with the service absent.

---

## 7. Enforcing the invariant (how R1–R4 are *tested*, not just promised)

Design is only real if CI fails when it is violated:

- **T-R4 dependency guard.** A test imports the full engine surface with GUI
  libraries blocked (`sys.modules["flask"]=None`, etc.) and asserts it still
  imports and runs — plus a static rule (import-linter / a grep test) that **no
  `scadavt.core.*` or engine module imports any L3/GUI module.** (Phase-1 grep is
  already clean; this freezes it.)
- **T-R3 round-trip.** `load(dump(cfg)) == cfg` (canonical), and a fixed set of
  configs survive Python→file→(GUI model)→file unchanged.
- **T-R2 single-validator.** For a battery of configs, the GUI path and the
  Python path return byte-identical validation results and identical normalised
  configs (same function, asserted).
- **T-R1 headless capability.** The existing live suite (`tools/live_core_*`,
  `tools/live_core_network.py`) runs with no GUI present — already true.
- **T-R5 API/CLI independence.** Import `scadavt.cli` and `scadavt.core.corelive`
  with both `flask` **and** the HTTP service module forced absent
  (`sys.modules[...]=None`); `scadavt validate/build/run` still work. Static rule:
  neither imports Flask nor the service module.
- **T-R6 designer water-independence.** Import the generic designer/service with
  `scadavt.apps.water` and the EPANET binding blocked; it still imports, loads a
  config, validates, and runs. Static rule: no `apps.water`/EPANET import in the
  generic designer.
- **T-R7 schema vs. semantic authority.** A config that passes JSON Schema but
  has a semantic fault (e.g. duplicate IP, dangling link endpoint) is **rejected
  by `coregen.validate`**; a config valid by schema but empty of devices fails
  validate. `validate` is asserted as the gate.
- **T-R8 meaning-preserving round-trip.** For a battery of configs incl.
  arbitrary non-grid ones: Python→file→(designer model)→file preserves the node
  set, the link set, the mapping, the rules, and every supported property
  (field-by-field diff == ∅); unknown-but-supported keys are retained.
- **T-R9 arbitrary topology.** A hand-authored non-grid config validates, builds,
  and runs (reusing the `--manual` live scenario) — no grid assumption anywhere
  in model/validator/engine.
- **T-R10 service security.** Default bind is `127.0.0.1`; a request to a
  non-local bind without a valid token is refused; engine + CLI run with the
  service never started.
- **T-R11 single CLI implementation.** `python -m scadavt` and the console-script
  resolve to the same `scadavt.cli:main`; a test asserts identical dispatch for a
  sample verb.

Acceptance of Phase 2 = **all** tests above green (T-R1…T-R11), plus a
GUI-authored config running unmodified through `scadavt run`, and a
`corelive`-authored arbitrary-topology config opening unmodified in the GUI.

---

## 8. Proposed work breakdown (post-approval, each on its own branch, no merge without approval)

1. **L0 model + schema** — `model.py`, `config.schema.json`, `load/dump`,
   `schema_version`; port `coregen` to consume them. Round-trip tests.
2. **L1 formalisation** — make `validate`/normalise the sole, schema-checked
   entry; single-validator test.
3. **Framework CLI** — one `scadavt.cli:main`, exposed as both `python -m
   scadavt` and a console-script (R11); move generic verbs out of `apps/water`,
   keep a water shim. (No new engine code.)
4. **Guard tests** — T-R4/T-R5/T-R6 dependency (static + runtime), T-R7, T-R11.
5. **Thin HTTP service** — wraps L1/L2 only; local-by-default + auth for remote
   (R10). Only after 1–4 green.
6. **Generic designer (GUI)** — water/EPANET-free (R6); lifts generic mechanics
   from `apps/water/labui`; supports arbitrary topologies (R9) and
   meaning-preserving round-trip (R8). Only after 5 green.

---

## 9. Decisions (approved, with constraints)

All four are **approved in principle**, bound by the refinements R5–R11 (§1):

- **D1 — GUI boundary → thin HTTP service** wrapping L1/L2. *Constraint:* the
  service is L3; the Python API/CLI never import it (R5); local-by-default and
  auth-gated for remote (R10).
- **D2 — GUI starting point → generalise `apps/water/labui`**, selectively.
  *Constraint:* the generic designer imports no water/EPANET code (R6); water is
  an optional overlay only.
- **D3 — Schema mechanism → JSON Schema (canonical) + typed accessors.**
  *Constraint:* schema is structural only; `coregen.validate` stays the
  authoritative semantic validator (R7); no runtime dep forced on the headless
  engine.
- **D4 — CLI → both `python -m scadavt` and a `scadavt` console-script.**
  *Constraint:* both dispatch to one `scadavt.cli:main` (R11).

---

## 10. Non-goals for Phase 2

Deployable local-PLC control, MTU-loss failover/watchdog, and multi-PLC attack
scenarios are tracked separately (PR #20 and follow-ups) and are **out of scope**
here; Phase 2 is strictly the dual-workflow architecture around the existing
engine.
