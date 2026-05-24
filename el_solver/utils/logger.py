"""Logger pakai rich, baca level dari settings."""
from __future__ import annotations

import logging

from rich.logging import RichHandler

from el_solver.config import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(rich_tracebacks=True, markup=False, show_path=False)
        handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
        # Hindari double-log dari root logger
        logger.propagate = False
    return logger
