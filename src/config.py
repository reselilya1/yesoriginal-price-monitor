"""Конфигурация.

Все секреты берутся ТОЛЬКО из переменных окружения (GitHub Secrets / .env).
В коде секретов нет и быть не должно.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --------------------------------------------------------------------------
# Пути
# --------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = Path(os.getenv("STATE_PATH", ROOT_DIR / "data" / "state.json"))

# --------------------------------------------------------------------------
# Google Sheets
# --------------------------------------------------------------------------

GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID", "18G299yWL8DWkal7Ty_XFmzmI0ZWp4mBKFZonWpkBRvE"
)
# gid конкретного листа. Пустая строка => первый лист книги.
GOOGLE_SHEET_GID = os.getenv("GOOGLE_SHEET_GID", "1297484631")

# Названия колонок, которые нам нужны (ищем по заголовку, не по номеру).
COL_ARTICLE = ("артикул", "articul", "article", "sku")
COL_QUANTITY = ("кількість", "количество", "qty", "quantity")
COL_SIZE_EU = ("розмір eu", "размер eu", "size eu", "eu")

# --------------------------------------------------------------------------
# Сайт
# --------------------------------------------------------------------------

BASE_URL = os.getenv("SITE_BASE_URL", "https://yesoriginal.com.ua").rstrip("/")
SEARCH_PATH = "/index.php?route=product/search&search={query}"

USER_AGENT = os.getenv(
    "USER_AGENT",
    "yesoriginal-price-monitor/1.0 (personal price tracker; 1 request per product per day)",
)

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "25"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.7"))  # пауза между запросами, сек
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "2.0"))  # 2, 4, 8 сек
# Сколько карточек из выдачи поиска проверять, если не было редиректа.
MAX_SEARCH_CANDIDATES = int(os.getenv("MAX_SEARCH_CANDIDATES", "2"))

# --------------------------------------------------------------------------
# Валидация цен
# --------------------------------------------------------------------------

MIN_VALID_PRICE = 1.0
MAX_VALID_PRICE = 10_000_000.0
# Изменение больше этого процента только ЛОГИРУЕТСЯ как аномалия.
# Уведомление всё равно отправляется — реальные скидки бывают и на -70%.
ANOMALY_PCT = float(os.getenv("ANOMALY_PCT", "80"))

# --------------------------------------------------------------------------
# Расписание
# --------------------------------------------------------------------------

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/Prague"))
CHECK_HOUR = int(os.getenv("CHECK_HOUR", "8"))
# GitHub Actions cron умеет только UTC, поэтому workflow стартует в 06:00 и 07:00 UTC,
# а скрипт сам решает, который из запусков соответствует 08:00 в Праге.
ENFORCE_SCHEDULE = os.getenv("ENFORCE_SCHEDULE", "false").lower() in {"1", "true", "yes"}

# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_LEN = 3900  # лимит Telegram 4096, оставляем запас


def mask_secret(value: str) -> str:
    """Для безопасного логирования."""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-2:]}"
