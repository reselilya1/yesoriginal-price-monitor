"""Хранилище предыдущих цен.

Реализация — обычный JSON-файл в репозитории, который workflow коммитит обратно.
Это самый надёжный бесплатный вариант для GitHub Actions:
    * состояние переживает любые перезапуски (в отличие от cache/artifacts,
      которые GitHub удаляет по TTL и по объёму);
    * история изменений видна в git diff;
    * файл можно поправить руками.

Весь доступ идёт через класс Store, поэтому переезд на SQLite —
это замена одного файла, без изменения остального кода.

Ключ записи: ARTICLE|SIZE (артикул в верхнем регистре).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from .sheets import article_key, normalize_size

log = logging.getLogger(__name__)

STATE_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_key(article: str, size_eu: str) -> str:
    return f"{article_key(article)}|{normalize_size(size_eu)}"


@dataclass
class PriceRecord:
    article: str
    size_eu: str
    current_price: float
    previous_price: Optional[float] = None
    product_url: str = ""
    product_name: str = ""
    first_seen_at: str = ""
    last_checked_at: str = ""
    last_changed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceRecord":
        known = {f: data.get(f) for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        known["article"] = known.get("article") or ""
        known["size_eu"] = known.get("size_eu") or ""
        known["current_price"] = float(known.get("current_price") or 0)
        prev = known.get("previous_price")
        known["previous_price"] = float(prev) if prev not in (None, "") else None
        for text_field in ("product_url", "product_name", "first_seen_at",
                           "last_checked_at", "last_changed_at"):
            known[text_field] = known.get(text_field) or ""
        return cls(**known)


@dataclass
class Store:
    path: Path
    items: Dict[str, PriceRecord] = field(default_factory=dict)
    url_cache: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    dirty: bool = False

    # ------------------------------------------------------------------ IO

    @classmethod
    def load(cls, path: Path) -> "Store":
        path = Path(path)
        if not path.exists():
            log.info("Файл состояния не найден (%s) — это первый запуск", path)
            return cls(path=path, meta={"version": STATE_VERSION})
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Файл состояния {path} повреждён ({exc}). "
                "Восстановите его из git history, чтобы не потерять базовые цены."
            ) from exc

        items = {
            key: PriceRecord.from_dict(value)
            for key, value in (raw.get("items") or {}).items()
        }
        store = cls(
            path=path,
            items=items,
            url_cache=dict(raw.get("url_cache") or {}),
            meta={k: v for k, v in raw.items() if k not in {"items", "url_cache"}},
        )
        store.meta.setdefault("version", STATE_VERSION)
        log.info("Состояние загружено: %s позиций, %s URL в кэше", len(items), len(store.url_cache))
        return store

    def save(self) -> bool:
        """Атомарная запись. Возвращает False, если писать было нечего."""
        if not self.dirty:
            log.info("Состояние не изменилось — файл не переписываю")
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            **{k: v for k, v in self.meta.items() if k != "version"},
            "updated_at": utc_now(),
            "url_cache": dict(sorted(self.url_cache.items())),
            "items": {k: self.items[k].to_dict() for k in sorted(self.items)},
        }
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent), delete=False, suffix=".tmp"
        )
        try:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, self.path)
        log.info("Состояние сохранено: %s позиций -> %s", len(self.items), self.path)
        return True

    # --------------------------------------------------------------- records

    @property
    def is_empty(self) -> bool:
        return not self.items

    def __iter__(self) -> Iterator[PriceRecord]:
        return iter(self.items.values())

    def get(self, article: str, size_eu: str) -> Optional[PriceRecord]:
        return self.items.get(make_key(article, size_eu))

    def upsert(
        self,
        article: str,
        size_eu: str,
        price: float,
        product_url: str,
        product_name: str,
    ) -> tuple[PriceRecord, Optional[float]]:
        """Записывает цену. Возвращает (запись, старая цена или None для новой позиции)."""
        key = make_key(article, size_eu)
        now = utc_now()
        record = self.items.get(key)

        if record is None:
            record = PriceRecord(
                article=article,
                size_eu=normalize_size(size_eu),
                current_price=price,
                previous_price=None,
                product_url=product_url,
                product_name=product_name,
                first_seen_at=now,
                last_checked_at=now,
                last_changed_at=now,
            )
            self.items[key] = record
            self.dirty = True
            return record, None

        old_price = record.current_price
        if price != old_price:
            record.previous_price = old_price
            record.current_price = price
            record.last_changed_at = now
            self.dirty = True
        if product_url and record.product_url != product_url:
            record.product_url = product_url
            self.dirty = True
        if product_name and record.product_name != product_name:
            record.product_name = product_name
            self.dirty = True
        record.last_checked_at = now
        self.dirty = True
        return record, old_price

    # ------------------------------------------------------------- url cache

    def get_url(self, article: str) -> Optional[str]:
        return self.url_cache.get(article_key(article))

    def set_url(self, article: str, url: str) -> None:
        key = article_key(article)
        if url and self.url_cache.get(key) != url:
            self.url_cache[key] = url
            self.dirty = True

    def forget_url(self, article: str) -> None:
        if self.url_cache.pop(article_key(article), None) is not None:
            self.dirty = True
