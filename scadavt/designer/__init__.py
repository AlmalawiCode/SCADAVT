"""The generic graphical network designer (Phase 2, Milestone 6).

Static, framework-free web app (``static/index.html`` + ``designer.js``) whose
in-memory document *is* the canonical config (:mod:`scadavt.core.model`), so a
Python→GUI→Python round-trip is lossless by construction. It is domain-neutral —
no water/EPANET — and performs no validation or execution itself: it calls the
shared HTTP service (:mod:`scadavt.service`), which wraps L1/L2.

The service serves it at ``/designer`` (same-origin), so it needs no CORS grant
and state-changing POSTs carry the same-origin ``Origin`` header the service
accepts.
"""
