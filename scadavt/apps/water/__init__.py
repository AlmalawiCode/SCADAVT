"""Water Distribution System — the reference SCADAVT application package.

Optional and domain-specific; the generic framework does not depend on it. It
provides, on top of the generic SCADAVT core:
  * ``wds/``        the EPANET physical model (engine, plant, demand)
  * ``wdsproto``    water process-variable types (level, flow, pressure, ...)
  * ``generate``    the EPANET ``.inp`` writer (design -> scenario)
  * ``app/``        the external process application exposing the public
                    Integration API (DesignApp/WaterApp, the app/gateway runners)
  * ``scenarios/``  EPANET networks + normal operating scenarios (town, ...)
  * ``labui/``      the EPANET web lab (optional UI)
Water-specific *attack scenarios* reuse the generic attack infrastructure in
``scadavt.attacks`` (ARP/proxy/handlers/mitm/dos/injection) and only add the
domain targets and expected physical consequences. See ``docs/EXTENDING.md`` for
how to add another application (power, gas, ...) without modifying the core.
"""
