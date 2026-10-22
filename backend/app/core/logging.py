"""Logging (Layer 7, item 7).

The backend's own diary for developers. Structured lines with request id, user id and run id.
Never written: passwords, tokens, API keys, or the user's code. As a second line of defence every
known secret value is masked in the final text of a log line (message and stack trace).
"""

import logging
import sys
from contextvars import ContextVar
from typing import TextIO

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")

LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "request_id=%(request_id)s user_id=%(user_id)s run_id=%(run_id)s %(message)s"
)
MASK = "***"

_HANDLER_NAME = "sangra"


class ContextFilter(logging.Filter):
    """Adds request_id, user_id and run_id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        record.run_id = run_id_var.get()
        return True


class RedactingFormatter(logging.Formatter):
    """Formats a record, then replaces every known secret value with ***."""

    def __init__(self, fmt: str, secrets: list[str]):
        super().__init__(fmt)
        # Longest first, so a secret that contains another one is masked whole.
        self._secrets = sorted({s for s in secrets if s}, key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return text


def setup_logging(level: str = "INFO", secrets: list[str] | None = None, stream: TextIO | None = None) -> None:
    """Install our one handler on the root logger. Safe to call again (it replaces the old handler)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler.get_name() == _HANDLER_NAME:
            root.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.addFilter(ContextFilter())
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, secrets or []))
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn's loggers go through our handler (same format, same masking).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
