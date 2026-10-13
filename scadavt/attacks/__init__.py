"""Controlled cyberattacks for the SCADAVT research lab.

Attacks are declarative (see ``catalog.Attack``) and run as isolated in-path
attacker containers (``mitm`` agent) added to the networked deployment by
``deploy.attacker_services`` and scheduled by ``runner.AttackRunner``. Every
attack is confined to one field network and its window is logged with wall and
simulated time, which is the label source for the IDS datasets.

Phase 2: False Data Injection (``fdi``) on a float sensor register.
"""

from .catalog import Attack, KINDS, resolve

__all__ = ["Attack", "KINDS", "resolve"]
