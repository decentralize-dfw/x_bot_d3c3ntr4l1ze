"""
utils/logger.py
---------------
Structured JSON logging — GitHub Actions ve log aggregator uyumlu.

Faz 3.1 — structured logging (rapor2.txt §3.1)
"""
import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra", None)
        if extra:
            log.update(extra)
        return json.dumps(log, ensure_ascii=False)


def get_logger(name: str) -> logging.Logger:
    """JSON-formatted logger. Her modül kendi adıyla çağırır."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
