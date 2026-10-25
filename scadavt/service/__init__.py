"""L3 — the optional thin HTTP service for SCADAVT (Phase 2, Milestone 5).

A small HTTP wrapper over the shared validator (L1) and execution engine (L2),
so the GUI designer (or any remote client) can validate / build / run configs it
never has to implement itself. It is a **leaf**: it depends on the engine, the
engine never depends on it. The Python API and CLI do not import it (R5).

Security posture (R10): binds ``127.0.0.1`` by default; any non-local bind
requires an auth token; state-changing requests (POST) are protected against
cross-site/browser-driven abuse (token header + Origin allow-list), so a local
browser visiting a hostile page cannot drive the service. Read-only ``GET``s
stay convenient. See :mod:`scadavt.service.app`.
"""

from .app import create_app

__all__ = ["create_app"]
