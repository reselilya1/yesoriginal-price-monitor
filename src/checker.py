"""Основная логика проверки цен."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config
from .parser import ProductSnapshot
from .sheets import TrackedItem, group_by_article
from .site import Site
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class PriceChange:
    article: str
    size_eu: str
    product_name: str
    product_url: str
    old_price: float
    new_price: float

    @property
    def delta(self) -> float:
        return self.new_price - self.old_price

    @property
    def pct(self) -> float:
        if not self.old_price:
            return 0.0
        return (self.new_price - self.old_price) / self.old_price * 100.0

    @property
    def direction(self) -> str:
        return "up" if self.delta > 0 else "down"

    @property
    def is_anomaly(self) -> bool:
        return abs(self.pct) > config.ANOMALY_PCT


@dataclass
class CheckResult:
    changes: List[PriceChange] = field(default_factory=list)
    baseline: bool = False
    rows_total: int = 0
    active_items: int = 0
    unique_articles: int = 0
    products_found: int = 0
    products_missing: int = 0
    sizes_missing: int = 0
    baseline_items: int = 0
    errors: int = 0
    requests_made: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


def _validate_price(price: Optional[float], article: str, size: str) -> bool:
    if price is None:
        log.warning("%s EU %s: цена не получена — пропускаю", article, size)
        return False
    if not isinstance(price, (int, float)) or price != price:  # NaN
        log.warning("%s EU %s: цена не является числом (%r) — пропускаю", article, size, price)
        return False
    if price < config.MIN_VALID_PRICE or price > config.MAX_VALID_PRICE:
        log.warning("%s EU %s: цена вне разумного диапазона (%s) — пропускаю", article, size, price)
        return False
    return True


def run_check(
    items: List[TrackedItem],
    store: Store,
    site: Optional[Site] = None,
) -> CheckResult:
    """Сверяет цены на сайте с сохранёнными и обновляет состояние.

    Ошибка по одному товару не останавливает обработку остальных.
    """
    site = site or Site()
    grouped = group_by_article(items)

    result = CheckResult(
        baseline=store.is_empty,
        active_items=len(items),
        unique_articles=len(grouped),
    )
    if result.baseline:
        log.info("Состояние пустое — это ПЕРВЫЙ ЗАПУСК, уведомления отправляться не будут")

    log.info("Активных позиций: %s", result.active_items)
    log.info("Уникальных артикулов: %s", result.unique_articles)

    for article, tracked in grouped.items():
        wanted_sizes = [t.size_eu for t in tracked]
        log.info("Проверяю артикул %s", article)
        try:
            resolved = site.resolve(article, store.get_url(article))
        except Exception as exc:  # noqa: BLE001 — один товар не должен ронять прогон
            log.exception("%s -> ERROR: %s", article, exc)
            result.errors += 1
            continue

        snapshot: Optional[ProductSnapshot] = resolved.snapshot
        if snapshot is None:
            if resolved.reason.startswith("temporary") or resolved.reason.startswith("search-failed"):
                # Сайт временно недоступен: НЕ трогаем сохранённые цены.
                log.warning("%s -> временная ошибка (%s), состояние не меняю", article, resolved.reason)
                result.errors += 1
            else:
                log.info("%s -> товар не найден, игнорирую", article)
                result.products_missing += 1
            continue

        result.products_found += 1
        store.set_url(article, resolved.url or snapshot.url)
        log.info("%s -> найден: %s", article, snapshot.name or snapshot.url)
        log.info("Размеры на сайте: %s", ", ".join(snapshot.sizes) or "—")
        log.info("Мои размеры: %s", ", ".join(wanted_sizes))
        if snapshot.duplicate_sizes:
            log.info(
                "Размеры в нескольких блоках наличия: %s (взят первый блок)",
                ", ".join(sorted(set(snapshot.duplicate_sizes))),
            )

        for tracked_item in tracked:
            size = tracked_item.size_eu
            offer = snapshot.price_for(size)
            if offer is None:
                # Размера нет / он недоступен: это НЕ изменение цены.
                log.info("Цена EU %s: размер отсутствует на сайте — пропускаю", size)
                result.sizes_missing += 1
                continue
            if not _validate_price(offer.price, article, size):
                result.errors += 1
                continue

            record, old_price = store.upsert(
                article=article,
                size_eu=size,
                price=offer.price,
                product_url=resolved.url or snapshot.url,
                product_name=snapshot.name,
            )

            if old_price is None:
                result.baseline_items += 1
                log.info("Цена EU %s: %s (базовая, уведомление не отправляется)", size, offer.price)
                continue
            if offer.price == old_price:
                log.info("Цена EU %s: без изменений (%s)", size, offer.price)
                continue

            change = PriceChange(
                article=article,
                size_eu=size,
                product_name=snapshot.name,
                product_url=record.product_url,
                old_price=old_price,
                new_price=offer.price,
            )
            log.info("Цена EU %s: %s -> %s", size, old_price, offer.price)
            if change.is_anomaly:
                log.warning(
                    "АНОМАЛИЯ: %s EU %s изменилась на %.2f%% (%s -> %s)",
                    article, size, change.pct, old_price, offer.price,
                )
            # Во время baseline-прогона (пустое состояние) уведомления не шлём.
            if not result.baseline:
                result.changes.append(change)

    result.requests_made = site.request_count
    log.info(
        "Проверка завершена. Найдено товаров: %s, не найдено: %s, "
        "размеров нет на сайте: %s, новых позиций: %s, ошибок: %s, HTTP-запросов: %s",
        result.products_found,
        result.products_missing,
        result.sizes_missing,
        result.baseline_items,
        result.errors,
        result.requests_made,
    )
    log.info("Изменений цен: %s", len(result.changes))
    return result
