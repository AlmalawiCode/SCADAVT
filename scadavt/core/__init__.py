"""SCADAVT domain-neutral core (M1).

This package is the reusable SCADA/ICS platform: the public Integration API, the
pluggable field-bus layer, the configurable control host, the event-stepped
timing loop, the minimal CORE session generator, and dataset labelling. Nothing
here knows about water, power or any particular industrial domain: it moves
*measurements*, *commands* and *actuator states* between configured SCADA
devices and external process applications that connect over the Integration API.

The water/EPANET simulator is NOT part of this package; it is an example
external application under ``scadavt.examples`` that talks the same API.
"""
