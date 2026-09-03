"""Конфигурация.

Все секреты берутся ТОЛЬКО из переменных окружения (GitHub Secrets / .env).
В коде секретов нет и быть не должно.
"""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo


# --------------------------------------------------------------------------
# Чтение переменных окружения
# --------------------------------------------------------------------------
#
# ВАЖНО: в GitHub Actions запись `ENV_VAR: ${{ vars.FOO }}` для несуществующей
# переменной даёт ПУСТУЮ СТРОКУ, а не отсутствие переменной. Обычный
# os.getenv("FOO", default) в этом случае вернёт "" вместо default, и всё
# падает (`ZoneInfo("")`, `int("")`). Поэтому пустое значение = «не задано».


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env_str(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env_str(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name, "").lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def env_tz(name: str, default: str) -> ZoneInfo:
    key = env_str(name, default)
    try:
        return ZoneInfo(key)
    except Exception:  # noqa: BLE001 — неизвестная зона не должна ронять бота
        return ZoneInfo(default)

# --------------------------------------------------------------------------
# Пути
# --------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = Path(env_str("STATE_PATH", str(ROOT_DIR / "data" / "state.json")))

# --------------------------------------------------------------------------
# Google Sheets
# --------------------------------------------------------------------------

GOOGLE_SHEET_ID = env_str("GOOGLE_SHEET_ID", "18G299yWL8DWkal7Ty_XFmzmI0ZWp4mBKFZonWpkBRvE")
# gid конкретного листа. Пустая строка => первый лист книги.
GOOGLE_SHEET_GID = env_str("GOOGLE_SHEET_GID", "1297484631")

# Названия колонок, которые нам нужны (ищем по заголовку, не по номеру).
COL_ARTICLE = ("артикул", "articul", "article", "sku")
COL_QUANTITY = ("кількість", "количество", "qty", "quantity")
COL_SIZE_EU = ("розмір eu", "размер eu", "size eu", "eu")

# --------------------------------------------------------------------------
# Сайт
# --------------------------------------------------------------------------

BASE_URL = env_str("SITE_BASE_URL", "https://yesoriginal.com.ua").rstrip("/")
SEARCH_PATH = "/index.php?route=product/search&search={query}"

USER_AGENT = env_str(
    "USER_AGENT",
    "yesoriginal-price-monitor/1.0 (personal price tracker; 1 request per product per day)",
)

REQUEST_TIMEOUT = env_float("REQUEST_TIMEOUT", 25.0)
REQUEST_DELAY = env_float("REQUEST_DELAY", 0.7)  # пауза между запросами, сек
MAX_RETRIES = env_int("MAX_RETRIES", 3)
RETRY_BACKOFF = env_float("RETRY_BACKOFF", 2.0)  # 2, 4, 8 сек
# Сколько карточек из выдачи поиска проверять, если не было редиректа.
MAX_SEARCH_CANDIDATES = env_int("MAX_SEARCH_CANDIDATES", 2)

# --------------------------------------------------------------------------
# Валидация цен
# --------------------------------------------------------------------------

MIN_VALID_PRICE = 1.0
MAX_VALID_PRICE = 10_000_000.0
# Изменение больше этого процента только ЛОГИРУЕТСЯ как аномалия.
# Уведомление всё равно отправляется — реальные скидки бывают и на -70%.
ANOMALY_PCT = env_float("ANOMALY_PCT", 80.0)

# --------------------------------------------------------------------------
# Расписание
# --------------------------------------------------------------------------

TIMEZONE = env_tz("TIMEZONE", "Europe/Prague")
CHECK_HOUR = env_int("CHECK_HOUR", 8)
# GitHub Actions cron умеет только UTC, поэтому workflow стартует в 06:00 и 07:00 UTC,
# а скрипт сам решает, который из запусков соответствует 08:00 в Праге.
ENFORCE_SCHEDULE = env_bool("ENFORCE_SCHEDULE", False)

# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env_str("TELEGRAM_CHAT_ID")
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_MAX_LEN = env_int("TELEGRAM_MAX_LEN", 3900)  # лимит Telegram 4096, оставляем запас


def mask_secret(value: str) -> str:
    """Для безопасного логирования."""
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}…{value[-2:]}"
