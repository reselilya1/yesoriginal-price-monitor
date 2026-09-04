"""Обработка команды /check из Telegram.

GitHub Actions — не постоянно работающий сервер, вебхук поставить некуда.
Поэтому используется long-poll-lite: workflow раз в несколько минут вызывает
getUpdates, и если пришла команда /check от разрешённого чата — запускает проверку.

Смещение (offset) нигде не хранится: Telegram сам удаляет апдейты из очереди,
когда мы подтверждаем их вызовом getUpdates(offset=last_id+1).

Команды от посторонних чатов игнорируются молча.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List

from . import config
from .logging_setup import setup_logging
from .notifier import Telegram, TelegramError
from .run_check import main as run_check_main

log = logging.getLogger(__name__)

KNOWN_COMMANDS = ("/check", "/start", "/help", "/status")


def _command_of(message: Dict[str, Any]) -> str:
    text = str(message.get("text") or "").strip()
    if not text.startswith("/"):
        return ""
    head = text.split()[0]
    return head.split("@", 1)[0].lower()


def main() -> int:
    setup_logging()
    telegram = Telegram()
    if not telegram.configured:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы")
        return 1

    try:
        updates: List[Dict[str, Any]] = telegram.get_updates()
    except TelegramError as exc:
        # Раньше здесь возвращался 0, и запуск выглядел успешным, хотя бот
        # не получал сообщений вообще. Теперь такой запуск краснеет.
        log.error("getUpdates не удался: %s", exc)
        if "conflict" in str(exc).lower() or "webhook" in str(exc).lower():
            log.error(
                "У бота установлен webhook — пока он есть, getUpdates работать не будет. "
                "Снять: откройте в браузере "
                "https://api.telegram.org/bot<ТОКЕН>/deleteWebhook"
            )
        return 1

    if not updates:
        log.info("Новых сообщений нет")
        return 0

    log.info("Получено апдейтов: %s", len(updates))
    last_id = max(int(u.get("update_id", 0)) for u in updates)

    # Подтверждаем СРАЗУ: иначе при падении проверки те же команды
    # прилетят снова и снова и мы зациклимся.
    try:
        telegram.confirm_updates(last_id)
    except TelegramError as exc:
        log.error("Не удалось подтвердить апдейты: %s", exc)

    wants_check = False
    for update in updates:
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        command = _command_of(message)
        if not command:
            continue
        if not telegram.is_authorized(chat_id):
            # Самая частая причина — опечатка в TELEGRAM_CHAT_ID. Полные id
            # в лог не пишем (репозиторий публичный), только длину и хвост:
            # этого хватает, чтобы заметить несовпадение.
            log.warning(
                "Команда %s отклонена: чат не совпадает с TELEGRAM_CHAT_ID "
                "(пришло: %s знаков, оканчивается на …%s; в секрете: %s знаков, "
                "оканчивается на …%s). Если это ваш чат — исправьте секрет.",
                command,
                len(str(chat_id)), str(chat_id)[-2:],
                len(telegram.chat_id), telegram.chat_id[-2:],
            )
            continue
        if command == "/check":
            wants_check = True
        elif command in ("/start", "/help"):
            _reply(
                telegram,
                "Бот следит за ценами на yesoriginal.com.ua по вашей Google Таблице.\n\n"
                f"Автоматическая проверка: каждый день в {config.CHECK_HOUR:02d}:00 "
                f"({config.TIMEZONE.key}).\n"
                "Команда /check — проверить прямо сейчас (ответ может занять пару минут).",
            )
        elif command == "/status":
            _reply(telegram, _status_text())

    if not wants_check:
        log.info("Команды /check не было")
        return 0

    log.info("Получена команда /check — запускаю проверку")
    _reply(telegram, "🔄 Запускаю проверку цен…")
    return run_check_main(["--report-always"])


def _reply(telegram: Telegram, text: str) -> None:
    try:
        telegram.send_message(text)
    except TelegramError as exc:
        log.error("Ответ не отправлен: %s", exc)


def _status_text() -> str:
    from .store import Store

    store = Store.load(config.STATE_PATH)
    updated = store.meta.get("updated_at", "—")
    return (
        f"📊 Отслеживается позиций: <b>{len(store.items)}</b>\n"
        f"Артикулов в кэше: <b>{len(store.url_cache)}</b>\n"
        f"Последнее обновление состояния: {updated}"
    )


if __name__ == "__main__":
    sys.exit(main())
