"""HTTP-доступ к yesoriginal.com.ua и поиск товара по артикулу.

Стратегия запросов (по одному запросу на артикул в день):

1. Если URL товара уже известен из state — идём сразу на карточку товара.
   Карточки разрешены в robots.txt, это самый дешёвый и вежливый путь.
2. Если URL неизвестен/устарел — используем поиск. Точное совпадение
   артикула отдаёт 302-редирект прямо на карточку.
3. Если редиректа не было — смотрим до MAX_SEARCH_CANDIDATES карточек
   из выдачи и проверяем в них артикул (model из JSON-LD).

Совпадение товара определяется ТОЛЬКО по артикулу. Название не участвует.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests

from . import config
from .parser import ProductSnapshot, extract_search_result_links, parse_product_page
from .sheets import article_key

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")


class TemporaryFetchError(RuntimeError):
    """Временная ошибка — имеет смысл повторить."""


class PermanentFetchError(RuntimeError):
    """Постоянная ошибка (404 и т.п.) — повторять бессмысленно."""


@dataclass
class ResolveResult:
    snapshot: Optional[ProductSnapshot]
    url: Optional[str]
    requests_made: int
    reason: str = ""


class Site:
    """Тонкая обёртка над requests с ретраями и паузами."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "uk-UA,uk;q=0.9",
            }
        )
        self._last_request_at = 0.0
        self.request_count = 0

    # ---------------------------------------------------------------- low level

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < config.REQUEST_DELAY:
            time.sleep(config.REQUEST_DELAY - elapsed)

    def get(self, url: str) -> requests.Response:
        """GET с паузой, таймаутом и ретраями только на временные ошибки."""
        last_error: Optional[Exception] = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            self._throttle()
            try:
                response = self.session.get(
                    url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True
                )
            except requests.RequestException as exc:
                last_error = exc
                log.warning("Сетевая ошибка (%s/%s) для %s: %s", attempt, config.MAX_RETRIES, url, exc)
            else:
                self.request_count += 1
                self._last_request_at = time.monotonic()
                if response.status_code == 404:
                    raise PermanentFetchError(f"404 для {url}")
                if response.status_code in (408, 425, 429, 500, 502, 503, 504):
                    last_error = TemporaryFetchError(
                        f"HTTP {response.status_code} для {url}"
                    )
                    log.warning(
                        "HTTP %s (%s/%s) для %s",
                        response.status_code,
                        attempt,
                        config.MAX_RETRIES,
                        url,
                    )
                elif response.status_code >= 400:
                    raise PermanentFetchError(f"HTTP {response.status_code} для {url}")
                else:
                    return response
            self._last_request_at = time.monotonic()
            if attempt < config.MAX_RETRIES:
                time.sleep(config.RETRY_BACKOFF ** attempt)
        raise TemporaryFetchError(str(last_error) if last_error else f"Не удалось получить {url}")

    # ---------------------------------------------------------------- high level

    def search_url(self, article: str) -> str:
        return config.BASE_URL + config.SEARCH_PATH.format(query=quote(article))

    def _snapshot_if_matches(
        self, response: requests.Response, article: str
    ) -> Optional[ProductSnapshot]:
        snapshot = parse_product_page(response.text, response.url)
        if snapshot is None:
            return None
        if article_key(snapshot.article) != article_key(article):
            log.debug(
                "Артикул на странице (%s) не совпадает с искомым (%s)",
                snapshot.article,
                article,
            )
            return None
        return snapshot

    def resolve(self, article: str, cached_url: Optional[str] = None) -> ResolveResult:
        """Находит товар по артикулу. Никогда не бросает исключение наружу."""
        made_before = self.request_count

        # 1. Прямой заход по известному URL
        if cached_url:
            try:
                response = self.get(cached_url)
                snapshot = self._snapshot_if_matches(response, article)
                if snapshot is not None:
                    return ResolveResult(
                        snapshot, snapshot.url or response.url,
                        self.request_count - made_before, "cached-url"
                    )
                log.info(
                    "Сохранённый URL для %s больше не соответствует артикулу — иду в поиск",
                    article,
                )
            except PermanentFetchError as exc:
                log.info("Сохранённый URL для %s недоступен (%s) — иду в поиск", article, exc)
            except TemporaryFetchError as exc:
                # Временная ошибка: НЕ считаем это «товар пропал»,
                # просто ничего не обновляем в этом прогоне.
                return ResolveResult(None, cached_url, self.request_count - made_before, f"temporary:{exc}")

        # 2. Поиск по артикулу
        try:
            response = self.get(self.search_url(article))
        except (TemporaryFetchError, PermanentFetchError) as exc:
            return ResolveResult(None, None, self.request_count - made_before, f"search-failed:{exc}")

        snapshot = self._snapshot_if_matches(response, article)
        if snapshot is not None:
            return ResolveResult(
                snapshot, snapshot.url or response.url,
                self.request_count - made_before, "search-redirect"
            )

        # 3. Кандидаты из выдачи
        wanted_slug = _slugify(article)
        candidates = extract_search_result_links(response.text, limit=10)
        ranked = sorted(
            candidates,
            key=lambda href: 0 if _slugify(href).endswith(wanted_slug) else 1,
        )
        for href in ranked[: max(0, config.MAX_SEARCH_CANDIDATES)]:
            try:
                candidate_response = self.get(href)
            except (TemporaryFetchError, PermanentFetchError):
                continue
            snapshot = self._snapshot_if_matches(candidate_response, article)
            if snapshot is not None:
                return ResolveResult(
                    snapshot, snapshot.url or candidate_response.url,
                    self.request_count - made_before, "search-candidate"
                )

        return ResolveResult(None, None, self.request_count - made_before, "not-found")
