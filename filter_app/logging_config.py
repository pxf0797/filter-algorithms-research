"""Loguru centralized configuration (P1-14).

Provides ``setup_logging()`` to configure Loguru output format:
- Dev mode: colored terminal output with DEBUG level
- Production mode: JSON output (ELK/Loki/Datadog compatible) with INFO level
"""

import sys
import json
import os
from loguru import logger


def setup_logging(production: bool = False) -> None:
    """Configure Loguru logger with environment-appropriate formatting.

    Parameters
    ----------
    production : bool
        If True, use JSON-formatted structured logging for production.
        If False (default), use colored terminal output.
    """
    logger.remove()  # Remove default handler

    if production:
        logger.add(
            sys.stderr,
            format=lambda record: json.dumps({
                "timestamp": record["time"].isoformat(),
                "level": record["level"].name,
                "message": record["message"],
                "module": record["module"],
                "function": record["function"],
                "line": record["line"],
            }, default=str),
            level="INFO",
            colorize=False,
        )
    else:
        logger.add(
            sys.stderr,
            format=(
                "<green>{time:HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            level="DEBUG",
            colorize=True,
        )


# Auto-configure on import if LOGURU_LEVEL is set or ENV=production
_production = os.environ.get("ENV") == "production"
if os.environ.get("LOGURU_LEVEL") or _production:
    setup_logging(production=_production)
