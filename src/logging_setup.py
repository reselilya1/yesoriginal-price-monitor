"""Логирование. Секреты в логи не попадают."""

from __future__ import annotations

import logging
import os
import re
import sys


class SecretFilter(logging.Filter):
    """Страховка: вырезает из логов токен и chat_id, если они туда случайно попали."""

    def __init__(self) -> None:
        super().__init__()
        secrets = [
            os.getenv("TELEGRAM_BOT_TOKEN", ""),
            os.getenv("TELEGRAM_CHAT_ID", ""),
        ]
        self._patterns = [re.escape(s) for s in secrets if s and len(s) > 4]
        self._token_re = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = self._token_re.sub("***", message)
        for pattern in self._patterns:
            cleaned = re.sub(pattern, "***", cleaned)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    handler.addFilter(SecretFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
