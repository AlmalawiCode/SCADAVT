"""Small helpers shared by the command-line programs."""

from __future__ import annotations

import logging


def hostport(text: str, default_port: int) -> tuple[str, int]:
    """Parse 'host:port' (or 'host') into a tuple."""
    if ":" in text:
        host, port = text.rsplit(":", 1)
        return host or "0.0.0.0", int(port)
    return text, default_port


def setup_logging(name: str, level: str = "INFO") -> logging.Logger:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=f"%(asctime)s {name} %(levelname)s %(message)s")
    return logging.getLogger(name)
