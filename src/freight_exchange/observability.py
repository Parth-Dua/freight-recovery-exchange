"""Structured, non-sensitive request logging.

Logs request_id, route, status and duration plus public Agentverse correlation
metadata (sender address, session). Never logs message text, AGENT_URI, keys,
auth headers or payment data.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
from typing import Any

LOGGER_NAME = "freight_exchange"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    return logger


def new_request_id() -> str:
    return f"req_{secrets.token_hex(6)}"


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    get_logger().log(level, json.dumps({"event": event, **fields}, default=str, sort_keys=True))
