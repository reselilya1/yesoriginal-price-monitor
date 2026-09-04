"""Тесты «здоровья» прогона.

Прогон, который обошёл все артикулы и не записал ни одной цены, — это поломка,
а не «товаров нет». Раньше такой запуск завершался успешно и молча, из-за чего
пустое состояние заметили только через сутки.
"""

import pathlib
import tempfile
import unittest

from src.checker import run_check
from src.sheets import TrackedItem
from src.site import ResolveResult
from tests.test_checker import FakeSite, snapshot


def many_items(count):
    return [TrackedItem(f"ART-{i:03d}", "42", 1) for i in range(count)]


class TestLooksBroken(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        from src.store import Store

        return Store.load(self.path)

    def test_nothing_recorded_across_many_articles_is_broken(self):
        items = many_items(30)
        site = FakeSite({})  # ни один артикул не резолвится
        result = run_check(items, self.store(), site)
        self.assertEqual(result.prices_recorded, 0)
        self.assertTrue(result.looks_broken)

    def test_site_errors_also_look_broken(self):
        items = many_items(30)
        site = FakeSite({
            f"ART-{i:03d}": ResolveResult(None, None, 1, "temporary:HTTP 403")
            for i in range(30)
        })
        result = run_check(items, self.store(), site)
        self.assertTrue(result.looks_broken)

    def test_healthy_run_is_not_broken(self):
        items = many_items(30)
        site = FakeSite({
            f"ART-{i:03d}": snapshot(article=f"ART-{i:03d}", prices={"42": 1000.0 + i})
            for i in range(30)
        })
        result = run_check(items, self.store(), site)
        self.assertEqual(result.prices_recorded, 30)
        self.assertFalse(result.looks_broken)

    def test_a_few_missing_products_is_normal(self):
        items = many_items(30)
        responses = {
            f"ART-{i:03d}": snapshot(article=f"ART-{i:03d}", prices={"42": 1000.0})
            for i in range(30)
        }
        for i in range(5):  # пять товаров реально отсутствуют в магазине
            responses[f"ART-{i:03d}"] = None
        result = run_check(items, self.store(), FakeSite(responses))
        self.assertEqual(result.products_missing, 5)
        self.assertEqual(result.prices_recorded, 25)
        self.assertFalse(result.looks_broken)

    def test_small_list_is_never_flagged(self):
        """На двух-трёх артикулах ноль записей — не повод считать это поломкой."""
        items = many_items(3)
        result = run_check(items, self.store(), FakeSite({}))
        self.assertEqual(result.prices_recorded, 0)
        self.assertFalse(result.looks_broken)

    def test_sizes_missing_everywhere_is_broken(self):
        """Товары находятся, но ни один размер не совпал — тоже поломка."""
        items = many_items(30)
        site = FakeSite({
            f"ART-{i:03d}": snapshot(article=f"ART-{i:03d}", prices={"99": 1000.0})
            for i in range(30)
        })
        result = run_check(items, self.store(), site)
        self.assertEqual(result.products_found, 30)
        self.assertEqual(result.sizes_missing, 30)
        self.assertTrue(result.looks_broken)


class TestResponseDiagnostics(unittest.TestCase):
    """Проверяем, что по ответу можно отличить товар от заглушки."""

    def setUp(self):
        from src.site import Site

        self.site = Site()

    def fake(self, text, headers=None, status=200):
        class R:
            pass

        r = R()
        r.text = text
        r.status_code = status
        r.headers = headers or {}
        return r

    def test_product_page_is_recognised(self):
        html = (
            '<html><head><title>Кросівки Nike DR7882-003</title>'
            '<script type="application/ld+json">{}</script></head>'
            '<body><input class="product-option"></body></html>'
        )
        text = self.site.describe_response(self.fake(html, {"server": "cloudflare", "cf-ray": "x"}))
        self.assertIn("есть-блок-размеров", text)
        self.assertIn("есть-json-ld", text)
        self.assertIn("cloudflare", text)
        self.assertIn("Кросівки Nike DR7882-003", text)

    def test_bot_challenge_is_flagged(self):
        html = (
            '<html><head><title>Just a moment...</title></head>'
            '<body><div id="challenge-platform"></div></body></html>'
        )
        text = self.site.describe_response(self.fake(html, {"server": "cloudflare", "cf-ray": "y"}, 403))
        self.assertIn("ЗАЩИТА-ОТ-БОТОВ", text)
        self.assertNotIn("есть-блок-размеров", text)
        self.assertIn("HTTP 403", text)

    def test_empty_response(self):
        text = self.site.describe_response(self.fake(""))
        self.assertIn("0 симв.", text)
        self.assertIn("признаки: нет", text)




class TestFailureReasons(unittest.TestCase):
    """Причина отказа должна попадать в сводку в читаемом виде."""

    def test_http_status_extracted(self):
        from src.checker import short_reason

        self.assertEqual(short_reason("search-failed:HTTP 403 для https://x"), "HTTP 403")
        self.assertEqual(short_reason("temporary:HTTP 503 для https://x"), "HTTP 503")

    def test_network_problems_named_in_russian(self):
        from src.checker import short_reason

        self.assertEqual(short_reason("search-failed:Read timed out"), "таймаут")
        self.assertEqual(short_reason("search-failed:Connection aborted"), "нет соединения")
        self.assertEqual(short_reason("not-found"), "товар не найден")

    def test_reasons_are_counted(self):
        import pathlib as _pathlib
        import tempfile as _tempfile

        from src.store import Store

        tmp = _tempfile.TemporaryDirectory()
        try:
            store = Store.load(_pathlib.Path(tmp.name) / "state.json")
            items = many_items(12)
            site = FakeSite({
                f"ART-{i:03d}": ResolveResult(None, None, 1, "search-failed:HTTP 403 для https://x")
                for i in range(12)
            })
            result = run_check(items, store, site)
            self.assertEqual(result.failure_reasons["HTTP 403"], 12)
            self.assertTrue(result.looks_broken)
        finally:
            tmp.cleanup()

if __name__ == "__main__":
    unittest.main()
