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
- **Both L3a and L3b sit on the same L0–L2.** The GUI has no private model,
  validator, or engine; it calls the same functions the Python user calls. This
  is R2, and it is what guarantees R3.
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

This directly serves **R3**: because there is exactly one model and one file
format, a Python-authored config and a GUI-authored config are the *same artefact*.

---

## 5. L1 — one validator, one normaliser

- `coregen.validate(cfg) -> list[issue]` remains the **only** validation entry
  point. The GUI calls it (per keystroke / on save); the CLI calls it; tests
  call it. There is no second, GUI-side validator — the GUI renders the issues
  L1 returns.
- Normalisation (defaulting, IP/mask inference, element↔register mapping) also
  lives here so both workflows see identical resolved configs.

Serves **R2**: identical rules, identical results, regardless of entry point.

---

## 6. L3 — the two workflows

### 6a. Python-first API + CLI (the primary, always-present workflow — R1)

- **Library API:** `scadavt.core.corelive` (Phase-1) — `build(core, cfg)`,
  `LiveNetwork.start/ping/probe/stop`, plus `grid()`/builder helpers for
  assembling large configs in code. Offline-testable plan via `build_plan`.
- **Framework CLI** (new, lifted out of `apps/water`): `python -m scadavt …`
  ```
  scadavt validate  config.json              # L1
  scadavt build      config.json -o out/      # L0→L2 generate artefacts
  scadavt run        config.json [--live]     # L2 run on CORE, print results
  scadavt scaffold   --plcs 12 --per-subnet 6 # emit a starting config (grid)
  scadavt export     config.json --format …   # round-trip / convert
  ```
- **Automation:** because everything is config-in / results-out, parameter
  sweeps, batch experiments, and CI runs are ordinary Python/shell loops over
  configs. No GUI, no display, no browser.

### 6b. Graphical network designer (optional — R4)

- A thin client that lets a user **draw** nodes (PLC/MTU/router/switch) and
  links, set IPs/points/rules, and **emit the canonical config file**; and
  **load** an existing config (Python- or GUI-authored) back onto the canvas.
- It contains **no** domain or validation logic: it POSTs/【calls】 the config to
  L1 for validation and L2 to run, and renders what comes back.
- Boundary options (decision D1 below): (i) **in-process** — a desktop/local app
  that imports L0–L2 directly; or (ii) **thin HTTP service** — a small server
  that *only* wraps L1/L2 (a few endpoints: `validate`, `build`, `run`,
  `load/save`) with zero business logic of its own. Either way the GUI is a leaf;
  the server, if any, is itself just another L3 client of the engine.

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

Acceptance of Phase 2 = these four tests green, plus a GUI-authored config
running unmodified through `scadavt run`, and a `corelive`-authored config
opening unmodified in the GUI.

---

## 8. Proposed work breakdown (post-approval, each on its own branch, no merge without approval)

1. **L0 model + schema** — `model.py`, `config.schema.json`, `load/dump`,
   `schema_version`; port `coregen` to consume them. Round-trip tests.
2. **L1 formalisation** — make `validate`/normalise the sole, schema-checked
   entry; single-validator test.
3. **Framework CLI** — `python -m scadavt`; move generic verbs out of
   `apps/water`; keep a water shim. (No new engine code.)
4. **Dependency guard tests** — T-R4 static + runtime.
5. **Generic designer (GUI)** — only after 1–4 are green; boundary per D1.

---

## 9. Open decisions (your call — needed to finalise)

- **D1 — GUI boundary:** in-process local app vs. thin HTTP service wrapping
  L1/L2. (Recommendation: thin HTTP service — keeps the GUI a pure client, works
  remotely, mirrors the existing `labui` serve model.)
- **D2 — GUI starting point:** generalise the existing `apps/water/labui` web UI
  into the domain-neutral designer, vs. a fresh designer. (Recommendation:
  generalise — it already calls `coregen` and proves the pattern.)
- **D3 — Schema mechanism:** JSON Schema + lightweight typed accessors vs.
  pydantic models as the source of truth. (Recommendation: JSON Schema canonical
  + typed accessors — no runtime dependency forced on the headless engine.)
- **D4 — CLI namespace/name:** `python -m scadavt <verb>` vs. a `scadavt`
  console-script. (Recommendation: both — module form always works, script is
  convenience.)

---

## 10. Non-goals for Phase 2

Deployable local-PLC control, MTU-loss failover/watchdog, and multi-PLC attack
scenarios are tracked separately (PR #20 and follow-ups) and are **out of scope**
here; Phase 2 is strictly the dual-workflow architecture around the existing
engine.
