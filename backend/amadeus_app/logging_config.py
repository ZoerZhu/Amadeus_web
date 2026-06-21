"""Structured logging configuration for Amadeus Web."""

from __future__ import annotations

import logging
import os
import sys

LOG_FORMAT = "[%(asctime)s] %(levelname)-7s %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Return a logger for the given module name."""
    return logging.getLogger(name)


def configure_logging(*, level: int | None = None) -> None:
    """Configure root logger with structured format.

    Set AMADEUS_LOG_LEVEL env var to override level (DEBUG, INFO, WARNING, ERROR).
    Default: INFO.
    """
    if level is None:
        raw_level = os.getenv("AMADEUS_LOG_LEVEL", "INFO").strip().upper()
        try:
            level = getattr(logging, raw_level)
        except AttributeError:
            level = logging.INFO

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Quiet noisy third-party loggers
    for name in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)

    root_logger.info("Logging configured at level %s.", logging.getLevelName(level))
