"""SCADAVT lab UI + API.

One clean API surface (``app.py``) drives everything; the drag-and-drop web
builder (``static/index.html``) and the terminal client (``cli.py``) are both
just clients of it. The preview simulation (``sim.py``) runs the real control
engine over a lightweight per-PLC process so a lab can be built, run and
attacked without CORE; real networked runs use the core modules + a CORE host
(see docs/CORE_RUNBOOK.md). Attacks can be scheduled or injected live — from the
UI or, as the researcher asked, from the terminal / a script.
"""
