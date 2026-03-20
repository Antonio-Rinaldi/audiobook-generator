from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"


def configure_logging(level: str = "INFO") -> None:
    resolved_level = getattr(logging, level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root_logger.addHandler(handler)

    root_logger.setLevel(resolved_level)


def create_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
