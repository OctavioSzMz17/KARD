"""
Application-wide logging configuration (Rules.md sections 10 & 11):
structured, single-line records carrying timestamp/level/logger/message,
verbosity controlled by `settings.LOG_LEVEL`.
"""

import logging
import sys

from core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging() -> None:
    """Configure the root logger once, at application startup."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root = logging.getLogger()

    if root.handlers:
        # Already configured (e.g. reload/import twice) — just sync the level.
        root.setLevel(level)
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
