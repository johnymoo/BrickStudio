"""Application-wide logging setup (structlog / JSON-friendly)."""
from __future__ import annotations

import logging
import sys
from typing import Any

from pythonjsonlogger import json as jsonlogger

from app.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    """Idempotently install a JSON stdlib logger.

    Tests / dev servers call this from the FastAPI lifespan; Celery workers
    call it from their boot path so worker logs are JSON too.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


__all__: list[Any] = ["configure_logging", "get_logger"]
