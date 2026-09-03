"""Telegram: отправка уведомлений и приём команды /check.

Токен и chat_id берутся только из переменных окружения и никогда не логируются.
Сообщения уходят ТОЛЬКО на TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import html
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

import requests

from . import config
from .checker import PriceChange

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Форматирование
# --------------------------------------------------------------------------


NBSP = "\u00a0"


def format_price(value: float) -> str:
    """3999 -> '3 999', 3999.5 -> '3 999.5' (неразрывный пробел)."""
    if float(value).is_integer():
        text = f"{int(value):,}".replace(",", NBSP)
    else:
        text = f"{value:,.2f}".replace(",", NBSP)
    return text


def format_change_block(change: PriceChange) -> str:
    if change.direction == "down":
        header = "📉 <b>Цена снизилась</b>"
    else:
        header = "📈 <b>Цена выросла</b>"

    sign = "+" if change.delta > 0 else "−"
    delta_abs = format_price(abs(change.delta))
    pct = f"{sign}{abs(change.pct):.2f}%"
    name = html.escape(change.product_name or change.article)

    lines = [
        header,
        f"<b>{name}</b>",
        f"Артикул: <code>{html.escape(change.article)}</code>",
        f"Размер: EU {html.escape(change.size_eu)}",
        f"Было: <b>{format_price(change.old_price)} ₴</b>",
        f"Стало: <b>{format_price(change.new_price)} ₴</b>",
        f"Изменение: <b>{sign}{delta_abs} ₴ ({pct})</b>",
    ]
    if change.product_url:
        lines.append(f'<a href="{html.escape(change.product_url, quote=True)}">Открыть товар</a>')
    return "\n".join(lines)


def build_messages(
    changes: Sequence[PriceChange],
    max_len: int = config.TELEGRAM_MAX_LEN,
) -> List[str]:
    """Одно сообщение на всю проверку; при переполнении режем на части."""
    if not changes:
        return []

    ordered = sorted(
        changes,
        key=lambda c: (0 if c.direction == "down" else 1, c.article, c.size_eu),
    )
    title = "🔔 <b>Изменение цен на YesOriginal</b>"
    footer = f"Всего изменений: <b>{len(ordered)}</b>"
    separator = "\n\n➖➖➖\n\n"

    blocks = [format_change_block(change) for change in ordered]

    pages: List[List[str]] = [[]]
    current_len = len(title) + 2
    for block in blocks:
        addition = len(block) + len(separator)
        if pages[-1] and current_len + addition + len(footer) + 4 > max_len:
            pages.append([])
            current_len = len(title) + 2
        pages[-1].append(block)
        current_len += addition

    messages: List[str] = []
    total_pages = len(pages)
    for index, page in enumerate(pages, start=1):
        head = title if total_pages == 1 else f"{title}  <i>(часть {index}/{total_pages})</i>"
        body = separator.join(page)
        text = f"{head}\n\n{body}"
        if index == total_pages:
            text += f"\n\n{footer}"
        messages.append(text)
    return messages


# --------------------------------------------------------------------------
# Клиент
# --------------------------------------------------------------------------


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.token = token if token is not None else config.TELEGRAM_BOT_TOKEN
        self.chat_id = str(chat_id if chat_id is not None else config.TELEGRAM_CHAT_ID)
        self.session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.token:
            raise TelegramError("TELEGRAM_BOT_TOKEN не задан")
        url = config.TELEGRAM_API.format(token=self.token, method=method)
        last_error: Optional[Exception] = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                response = self.session.post(url, json=payload, timeout=config.REQUEST_TIMEOUT)
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("Telegram %s: попытка %s не удалась (%s)", method, attempt, exc)
            else:
                if data.get("ok"):
                    return data
                description = str(data.get("description", ""))
                if response.status_code == 429:
                    delay = int((data.get("parameters") or {}).get("retry_after", 5))
                    log.warning("Telegram rate limit, жду %s с", delay)
                    time.sleep(delay)
                    last_error = TelegramError(description)
                    continue
                # Ошибки вида 400 (плохой chat_id, битая разметка) повторять бессмысленно
                raise TelegramError(f"{method}: {description or response.status_code}")
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF ** attempt)
        raise TelegramError(f"{method}: {last_error}")

    # ------------------------------------------------------------------ send

    def send_message(self, text: str, chat_id: Optional[str] = None) -> None:
        target = str(chat_id or self.chat_id)
        if not target:
            raise TelegramError("TELEGRAM_CHAT_ID не задан")
        self._call(
            "sendMessage",
            {
                "chat_id": target,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    def send_changes(self, changes: Sequence[PriceChange], chat_id: Optional[str] = None) -> int:
        messages = build_messages(changes)
        for text in messages:
            self.send_message(text, chat_id=chat_id)
            if len(messages) > 1:
                time.sleep(0.5)
        return len(messages)

    # --------------------------------------------------------------- receive

    def get_updates(self, offset: Optional[int] = None, timeout: int = 0) -> List[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        data = self._call("getUpdates", payload)
        return list(data.get("result") or [])

    def confirm_updates(self, last_update_id: int) -> None:
        """Подтверждаем обработку: Telegram удалит эти апдейты из очереди."""
        self._call("getUpdates", {"offset": last_update_id + 1, "timeout": 0})

    def is_authorized(self, chat_id: object) -> bool:
        return str(chat_id) == str(self.chat_id)
