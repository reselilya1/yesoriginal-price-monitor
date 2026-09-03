"""Разбор HTML страницы товара yesoriginal.com.ua.

Что установлено исследованием реальных страниц (см. README, раздел «Как устроен сайт»):

* Страница отдаётся сервером целиком, JavaScript для получения цен не нужен.
* Артикул лежит в JSON-LD: {"@type":"Product", "model":"DR7882-003", ...}
* Каждый размер — это <input class="product-option"> с атрибутами
      data-price   — базовая (зачёркнутая) цена
      data-special — цена со скидкой; 0 означает «скидки нет»
  и парным <label for="...">42</label> с текстом размера.
* КОНЕЧНАЯ ЦЕНА = data-special, если он > 0, иначе data-price.
* offers.price из JSON-LD брать НЕЛЬЗЯ — это цена только предвыбранного размера.
* Недоступные размеры на странице просто не выводятся.
* Блоков размеров может быть несколько (быстрая доставка по Украине / доставка из EU),
  и один и тот же размер может встречаться в двух блоках с разной ценой.
  Берём первый блок в порядке вёрстки, остальное логируем.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from .sheets import normalize_size

log = logging.getLogger(__name__)

_SECTION_CLASS_RE = re.compile(r"^option_\d+$")
_SIZE_GROUP_RE = re.compile(r"розм|разм|size", re.IGNORECASE)
_UNAVAILABLE_RE = re.compile(r"нема[єюї]|відсутн|отсутств|out\s*of\s*stock", re.IGNORECASE)


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001 — lxml не обязателен
        return BeautifulSoup(html, "html.parser")


def _to_float(raw: object) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).replace(" ", "").replace(" ", "").replace(",", ".").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Модели
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SizeOffer:
    """Одно предложение: конкретный размер конкретного товара."""

    size_eu: str
    price: float          # конечная цена, которую платит покупатель
    base_price: float     # «зачёркнутая» цена (для справки, для мониторинга НЕ используется)
    section_index: int
    section_label: str

    @property
    def has_discount(self) -> bool:
        return self.base_price > self.price


@dataclass
class ProductSnapshot:
    """Всё, что нам нужно со страницы товара."""

    article: str
    name: str
    url: str
    offers: Dict[str, SizeOffer] = field(default_factory=dict)
    duplicate_sizes: List[str] = field(default_factory=list)
    brand: str = ""

    @property
    def sizes(self) -> List[str]:
        return list(self.offers.keys())

    def price_for(self, size_eu: str) -> Optional[SizeOffer]:
        return self.offers.get(normalize_size(size_eu))


# --------------------------------------------------------------------------
# JSON-LD
# --------------------------------------------------------------------------


def _iter_json_ld(soup: BeautifulSoup):
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node and isinstance(node["@graph"], list):
                    stack.extend(node["@graph"])
                yield node


def extract_product_ld(soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    for node in _iter_json_ld(soup):
        if node.get("@type") == "Product" and node.get("model"):
            return node
    for node in _iter_json_ld(soup):
        if node.get("@type") == "Product":
            return node
    return None


# --------------------------------------------------------------------------
# Разбор размеров
# --------------------------------------------------------------------------


def _label_map(soup: BeautifulSoup) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for label in soup.find_all("label"):
        target = label.get("for")
        if target:
            labels[target] = label.get_text(" ", strip=True)
    return labels


def extract_size_offers(soup: BeautifulSoup) -> (Dict[str, SizeOffer], List[str]):
    """Возвращает {нормализованный размер -> SizeOffer} и список размеров-дублей."""
    labels = _label_map(soup)
    offers: Dict[str, SizeOffer] = {}
    duplicates: List[str] = []

    section_index = -1
    section_label = ""
    section_unavailable = False

    for tag in soup.find_all(True):
        classes = tag.get("class") or []

        # Заголовок секции наличия: <div class="... option_13">В наявності (...)</div>
        if tag.name == "div" and any(_SECTION_CLASS_RE.match(c) for c in classes):
            section_index += 1
            section_label = tag.get_text(" ", strip=True)
            section_unavailable = bool(_UNAVAILABLE_RE.search(section_label))
            continue

        if tag.name != "input" or "product-option" not in classes:
            continue

        # Отсекаем опции, которые не про размер (цвет, комплектация и т.п.)
        group = tag.find_parent(attrs={"role": "group"})
        aria = (group.get("aria-label") if group else None) or ""
        if aria and not _SIZE_GROUP_RE.search(aria):
            continue

        if section_unavailable:
            log.debug("Размер пропущен: секция '%s' помечена как отсутствующая", section_label)
            continue
        if tag.has_attr("disabled"):
            continue

        raw_size = labels.get(tag.get("id", ""), "")
        size = normalize_size(raw_size)
        if not size:
            continue

        base = _to_float(tag.get("data-price"))
        special = _to_float(tag.get("data-special"))
        if base is None and special is None:
            log.warning("У размера %s нет ни data-price, ни data-special — пропускаю", size)
            continue

        # Ключевое правило: special == 0 означает «скидки нет».
        if special is not None and special > 0:
            final = special
        elif base is not None:
            final = base
        else:
            continue

        if final <= 0:
            log.warning("Размер %s: нечисловая/нулевая цена, пропускаю", size)
            continue

        offer = SizeOffer(
            size_eu=size,
            price=final,
            base_price=base if base is not None else final,
            section_index=max(section_index, 0),
            section_label=section_label,
        )
        if size in offers:
            duplicates.append(size)
            log.info(
                "Размер %s встречается повторно (секция '%s', цена %s) — "
                "оставляю первое вхождение %s из '%s'",
                size,
                section_label,
                final,
                offers[size].price,
                offers[size].section_label,
            )
            continue
        offers[size] = offer

    return offers, duplicates


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------


def parse_product_page(html: str, url: str = "") -> Optional[ProductSnapshot]:
    """HTML -> ProductSnapshot. None, если это не страница товара."""
    if not html or len(html) < 500:
        log.warning("Слишком короткий ответ (%s байт) — похоже на битую страницу", len(html or ""))
        return None

    soup = _make_soup(html)
    product = extract_product_ld(soup)
    if not product:
        log.debug("JSON-LD Product на странице не найден")
        return None

    article = str(product.get("model") or "").strip()
    if not article:
        log.warning("В JSON-LD нет поля model (артикул) — товар не идентифицирован")
        return None

    name = str(product.get("name") or "").strip()
    canonical = soup.find("link", attrs={"rel": "canonical"})
    page_url = (
        str(product.get("url") or "")
        or (canonical.get("href") if canonical else "")
        or url
    )
    brand = product.get("brand") or ""
    if isinstance(brand, dict):
        brand = brand.get("name", "")

    offers, duplicates = extract_size_offers(soup)

    return ProductSnapshot(
        article=article,
        name=name,
        url=page_url,
        offers=offers,
        duplicate_sizes=duplicates,
        brand=str(brand),
    )


def extract_search_result_links(html: str, limit: int = 10) -> List[str]:
    """Ссылки на карточки товаров со страницы результатов поиска."""
    soup = _make_soup(html)
    links: List[str] = []
    for card in soup.select("div.product-thumb"):
        anchor = card.find("a", href=True)
        if not anchor:
            continue
        href = anchor["href"]
        if href not in links:
            links.append(href)
        if len(links) >= limit:
            break
    return links
