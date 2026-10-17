"""Structured logging setup (key=value text by default, JSON when LOG_JSON=true)."""

import json
import logging
import sys
from datetime import datetime, timezone

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _KvFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in vars(record).items() if k not in _RESERVED}
        if extras:
            base += " " + " ".join(f"{k}={v!r}" for k, v in extras.items())
        return base


def setup_logging(level: str = "INFO", json_logs: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_logs:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(_KvFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Usage: log = get_logger(__name__); log.info("thing happened", extra={"user_id": uid})"""
    return logging.getLogger(name)
