"""Чтение публичной Google Таблицы через CSV-экспорт.

OAuth не нужен: таблица публичная, у Google есть официальный CSV-экспорт
    https://docs.google.com/spreadsheets/d/<ID>/export?format=csv&gid=<GID>

Нас интересуют ровно три колонки, которые ищутся ПО ЗАГОЛОВКУ:
    Артикул, Кількість, Розмір EU
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set

import requests

from . import config

log = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


# --------------------------------------------------------------------------
# Нормализация
# --------------------------------------------------------------------------


def normalize_article(raw: object) -> str:
    """Убираем случайные пробелы, но НЕ меняем смысл артикула.

    'DX1487-016 '      -> 'DX1487-016'
    ' 50467556  002 '  -> '50467556 002'   (внутренний пробел — часть артикула)
    Регистр сохраняется как есть.
    """
    if raw is None:
        return ""
    text = str(raw).replace(" ", " ").replace(" ", " ")
    return _WS_RE.sub(" ", text).strip()


def article_key(article: str) -> str:
    """Ключ для сравнения артикулов (регистронезависимо)."""
    return normalize_article(article).upper()


def normalize_size(raw: object) -> str:
    """Приводим размер к каноническому виду.

    '44,5' -> '44.5'   '42.0' -> '42'   ' m ' -> 'M'   '42-46' -> '42-46'
    """
    if raw is None:
        return ""
    text = str(raw).replace(" ", " ").strip()
    text = _WS_RE.sub(" ", text)
    if not text:
        return ""
    candidate = text.replace(",", ".")
    # Чисто числовой размер: 42, 42.5, 42.0
    if re.fullmatch(r"\d+(?:\.\d+)?", candidate):
        value = float(candidate)
        if value.is_integer():
            return str(int(value))
        return ("%f" % value).rstrip("0").rstrip(".")
    return text.upper()


def parse_quantity(raw: object) -> int:
    """'2' -> 2, '' -> 0, '1 шт' -> 1, мусор -> 0."""
    if raw is None:
        return 0
    match = _NUM_RE.search(str(raw).replace(" ", " "))
    if not match:
        return 0
    try:
        return int(float(match.group(0).replace(",", ".")))
    except ValueError:
        return 0


def _header_key(value: str) -> str:
    return _WS_RE.sub(" ", str(value or "").replace(" ", " ")).strip().lower()


# --------------------------------------------------------------------------
# Модель строки
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackedItem:
    """Одна отслеживаемая комбинация артикул + размер."""

    article: str
    size_eu: str
    quantity: int

    @property
    def key(self) -> str:
        return f"{article_key(self.article)}|{self.size_eu}"


# --------------------------------------------------------------------------
# Разбор CSV
# --------------------------------------------------------------------------


def _find_column(headers: Sequence[str], candidates: Iterable[str]) -> Optional[int]:
    normalized = [_header_key(h) for h in headers]
    wanted = [c.lower() for c in candidates]
    # 1) точное совпадение
    for want in wanted:
        if want in normalized:
            return normalized.index(want)
    # 2) заголовок начинается с искомого ('Розмір EU (взуття)')
    for want in wanted:
        for idx, name in enumerate(normalized):
            if name.startswith(want):
                return idx
    return None


def _locate_header_row(rows: List[List[str]]) -> int:
    """Ищем строку заголовков (обычно первая, но бывает шапка сверху)."""
    for idx, row in enumerate(rows[:10]):
        if (
            _find_column(row, config.COL_ARTICLE) is not None
            and _find_column(row, config.COL_QUANTITY) is not None
            and _find_column(row, config.COL_SIZE_EU) is not None
        ):
            return idx
    raise ValueError(
        "Не найдена строка заголовков с колонками "
        "'Артикул', 'Кількість' и 'Розмір EU'. Проверьте лист/gid таблицы."
    )


def parse_sheet_csv(csv_text: str) -> List[TrackedItem]:
    """CSV -> список отслеживаемых позиций (только Кількість > 0, без дублей)."""
    rows = [row for row in csv.reader(io.StringIO(csv_text))]
    if not rows:
        raise ValueError("Google Sheets вернул пустой CSV")

    header_idx = _locate_header_row(rows)
    headers = rows[header_idx]
    i_article = _find_column(headers, config.COL_ARTICLE)
    i_qty = _find_column(headers, config.COL_QUANTITY)
    i_size = _find_column(headers, config.COL_SIZE_EU)
    log.info(
        "Колонки найдены по заголовкам: Артикул=%s, Кількість=%s, Розмір EU=%s",
        i_article,
        i_qty,
        i_size,
    )

    seen: Set[str] = set()
    items: List[TrackedItem] = []
    total = skipped_zero = skipped_empty = duplicates = 0

    for row in rows[header_idx + 1 :]:
        if not any(str(cell).strip() for cell in row):
            continue  # полностью пустая строка
        total += 1

        def cell(index: Optional[int]) -> str:
            if index is None or index >= len(row):
                return ""
            return row[index]

        article = normalize_article(cell(i_article))
        size = normalize_size(cell(i_size))
        quantity = parse_quantity(cell(i_qty))

        if not article or not size:
            skipped_empty += 1
            continue
        if quantity <= 0:
            skipped_zero += 1
            continue

        item = TrackedItem(article=article, size_eu=size, quantity=quantity)
        if item.key in seen:
            duplicates += 1
            continue
        seen.add(item.key)
        items.append(item)

    log.info(
        "Загружено строк из Google Sheets: %s (активных: %s, Кількість=0: %s, "
        "пустых/битых: %s, дубликатов: %s)",
        total,
        len(items),
        skipped_zero,
        skipped_empty,
        duplicates,
    )
    return items


def group_by_article(items: Iterable[TrackedItem]) -> Dict[str, List[TrackedItem]]:
    """Группируем размеры по артикулу — один HTTP-запрос на артикул."""
    grouped: Dict[str, List[TrackedItem]] = {}
    for item in items:
        grouped.setdefault(item.article, []).append(item)
    for sizes in grouped.values():
        sizes.sort(key=lambda i: i.size_eu)
    return grouped


# --------------------------------------------------------------------------
# Загрузка
# --------------------------------------------------------------------------


def build_csv_url(sheet_id: str, gid: str = "") -> str:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        url += f"&gid={gid}"
    return url


def fetch_sheet_csv(
    sheet_id: Optional[str] = None,
    gid: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> str:
    sheet_id = sheet_id or config.GOOGLE_SHEET_ID
    gid = config.GOOGLE_SHEET_GID if gid is None else gid
    url = build_csv_url(sheet_id, gid)
    log.info("Читаю Google Sheets: %s", url)

    sess = session or requests.Session()
    last_error: Optional[Exception] = None
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            response = sess.get(
                url,
                timeout=config.REQUEST_TIMEOUT,
                headers={"User-Agent": config.USER_AGENT},
                allow_redirects=True,
            )
            response.raise_for_status()
            response.encoding = response.encoding or "utf-8"
            text = response.text
            if "<html" in text[:200].lower():
                raise ValueError(
                    "Google вернул HTML вместо CSV — вероятно, таблица не публичная. "
                    "Откройте доступ 'Всем, у кого есть ссылка (Читатель)'."
                )
            return text
        except Exception as exc:  # noqa: BLE001 — ретраим любую сетевую проблему
            last_error = exc
            log.warning(
                "Попытка %s/%s чтения Google Sheets не удалась: %s",
                attempt,
                config.MAX_RETRIES,
                exc,
            )
            if attempt < config.MAX_RETRIES:
                import time

                time.sleep(config.RETRY_BACKOFF ** attempt)
    raise RuntimeError(f"Не удалось прочитать Google Sheets: {last_error}")


def load_tracked_items(
    sheet_id: Optional[str] = None,
    gid: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> List[TrackedItem]:
    return parse_sheet_csv(fetch_sheet_csv(sheet_id, gid, session))
