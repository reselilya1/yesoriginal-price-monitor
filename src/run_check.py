"""Точка входа: проверка цен.

    python -m src.run_check                  # обычный прогон
    python -m src.run_check --dry-run        # без Telegram и без записи состояния
    python -m src.run_check --report-always  # ответить даже если изменений нет (для /check)

Ежедневный workflow запускается дважды (06:00 и 07:00 UTC), потому что cron
в GitHub Actions понимает только UTC. Переменная ENFORCE_SCHEDULE=true заставляет
скрипт выполниться только тогда, когда в Праге действительно 08:00 —
так переход CET/CEST обрабатывается автоматически.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from . import config
from .checker import run_check
from .logging_setup import setup_logging
from .notifier import Telegram, TelegramError
from .sheets import load_tracked_items
from .site import Site
from .store import Store

log = logging.getLogger(__name__)


def schedule_allows_run() -> bool:
    now = datetime.now(config.TIMEZONE)
    if now.hour != config.CHECK_HOUR:
        log.info(
            "Сейчас %s (%s), а проверка запланирована на %02d:00 — этот запуск пропускаю "
            "(так обрабатывается переход CET/CEST).",
            now.strftime("%Y-%m-%d %H:%M"),
            config.TIMEZONE.key,
            config.CHECK_HOUR,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка цен на yesoriginal.com.ua")
    parser.add_argument("--dry-run", action="store_true",
                        help="не отправлять Telegram и не сохранять состояние")
    parser.add_argument("--report-always", action="store_true",
                        help="ответить в Telegram даже если изменений нет (команда /check)")
    parser.add_argument("--reply-chat", default=None,
                        help="chat_id для ответа (по умолчанию TELEGRAM_CHAT_ID)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    setup_logging(args.log_level)
    log.info("Starting price check")

    if config.ENFORCE_SCHEDULE and not schedule_allows_run():
        return 0

    telegram = Telegram()
    if not args.dry_run and not telegram.configured:
        log.error("Не заданы TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID — уведомления отправить нельзя")

    try:
        items = load_tracked_items()
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось прочитать Google Sheets: %s", exc)
        return 1

    if not items:
        log.warning("В таблице нет ни одной позиции с Кількість > 0 — делать нечего")
        return 0

    store = Store.load(config.STATE_PATH)
    was_baseline = store.is_empty

    result = run_check(items, store, Site())

    if args.dry_run:
        log.info("--dry-run: состояние не сохраняю, Telegram не трогаю")
    else:
        store.save()

    if was_baseline:
        log.info(
            "Первый запуск: сохранено %s базовых цен, уведомления НЕ отправляются",
            result.baseline_items,
        )
        if args.report_always and not args.dry_run and telegram.configured:
            _safe_send(
                telegram,
                args.reply_chat,
                "✅ Первая проверка завершена. Сохранено "
                f"<b>{result.baseline_items}</b> начальных цен. "
                "Уведомления начнут приходить со следующей проверки.",
            )
        return 0

    if result.has_changes and not args.dry_run and telegram.configured:
        try:
            parts = telegram.send_changes(result.changes, chat_id=args.reply_chat)
            log.info("Уведомление отправлено (%s сообщ.)", parts)
        except TelegramError as exc:
            log.error("Не удалось отправить уведомление: %s", exc)
            return 1
    elif not result.has_changes:
        log.info("Изменений нет — Telegram не беспокоим")
        if args.report_always and not args.dry_run and telegram.configured:
            _safe_send(
                telegram,
                args.reply_chat,
                "✅ Проверка завершена. Изменений цен не обнаружено.",
            )

    return 0


def _safe_send(telegram: Telegram, chat_id: str | None, text: str) -> None:
    try:
        telegram.send_message(text, chat_id=chat_id)
    except TelegramError as exc:
        log.error("Не удалось отправить сообщение: %s", exc)


if __name__ == "__main__":
    sys.exit(main())
