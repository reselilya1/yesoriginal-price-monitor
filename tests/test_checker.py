"""Тесты логики сравнения цен, baseline и защиты от ложных уведомлений."""

import pathlib
import tempfile
import unittest

from src.checker import run_check
from src.parser import ProductSnapshot, SizeOffer
from src.sheets import TrackedItem
from src.site import ResolveResult
from src.store import Store

URL = "https://yesoriginal.com.ua/uk/test-dd1503-101"


def snapshot(article="DD1503-101", name="Nike Dunk Low Panda", prices=None):
    prices = prices or {"42": 3999.0, "43": 4599.0}
    offers = {
        size: SizeOffer(size, price, price, 0, "В наявності")
        for size, price in prices.items()
    }
    return ProductSnapshot(article=article, name=name, url=URL, offers=offers)


class FakeSite:
    """Подставной сайт: отдаёт заранее заданные ответы, запросов не делает."""

    def __init__(self, responses):
        self.responses = responses
        self.request_count = 0
        self.calls = []

    def resolve(self, article, cached_url=None):
        self.calls.append((article, cached_url))
        self.request_count += 1
        value = self.responses.get(article)
        if isinstance(value, ResolveResult):
            return value
        if value is None:
            return ResolveResult(None, None, 1, "not-found")
        return ResolveResult(value, value.url, 1, "search-redirect")


class CheckerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def fresh_store(self):
        return Store.load(self.path)

    def baseline(self, prices=None):
        """Создаёт baseline и возвращает сохранённое состояние."""
        store = self.fresh_store()
        items = [TrackedItem("DD1503-101", "42", 2), TrackedItem("DD1503-101", "43", 1)]
        result = run_check(items, store, FakeSite({"DD1503-101": snapshot(prices=prices)}))
        store.save()
        return result


class TestFirstRun(CheckerTestCase):
    def test_first_run_sends_nothing(self):
        result = self.baseline()
        self.assertTrue(result.baseline)
        self.assertEqual(result.changes, [])
        self.assertEqual(result.baseline_items, 2)

    def test_first_run_stores_prices(self):
        self.baseline()
        store = self.fresh_store()
        self.assertEqual(store.get("DD1503-101", "42").current_price, 3999)
        self.assertEqual(store.get("DD1503-101", "43").current_price, 4599)
        self.assertIsNone(store.get("DD1503-101", "42").previous_price)


class TestPriceComparison(CheckerTestCase):
    def setUp(self):
        super().setUp()
        self.baseline()
        self.items = [TrackedItem("DD1503-101", "42", 2), TrackedItem("DD1503-101", "43", 1)]

    def run_with(self, prices):
        store = self.fresh_store()
        result = run_check(self.items, store, FakeSite({"DD1503-101": snapshot(prices=prices)}))
        store.save()
        return result

    def test_price_down(self):
        result = self.run_with({"42": 3799.0, "43": 4599.0})
        self.assertEqual(len(result.changes), 1)
        change = result.changes[0]
        self.assertEqual((change.old_price, change.new_price), (3999, 3799))
        self.assertEqual(change.direction, "down")
        self.assertAlmostEqual(change.delta, -200)
        self.assertAlmostEqual(change.pct, -5.001250312578, places=6)

    def test_price_up(self):
        result = self.run_with({"42": 4499.0, "43": 4599.0})
        self.assertEqual(len(result.changes), 1)
        change = result.changes[0]
        self.assertEqual(change.direction, "up")
        self.assertAlmostEqual(change.delta, 500)
        self.assertAlmostEqual(change.pct, 12.503125781, places=6)

    def test_one_uah_change_is_reported(self):
        result = self.run_with({"42": 3998.0, "43": 4599.0})
        self.assertEqual(len(result.changes), 1)
        self.assertAlmostEqual(result.changes[0].delta, -1)

    def test_no_change(self):
        result = self.run_with({"42": 3999.0, "43": 4599.0})
        self.assertEqual(result.changes, [])

    def test_only_changed_size_reported(self):
        result = self.run_with({"42": 3699.0, "43": 4599.0})
        self.assertEqual([c.size_eu for c in result.changes], ["42"])

    def test_both_sizes_changed(self):
        result = self.run_with({"42": 3699.0, "43": 4499.0})
        self.assertEqual(sorted(c.size_eu for c in result.changes), ["42", "43"])

    def test_same_price_stored_per_size_separately(self):
        self.run_with({"42": 3500.0, "43": 3500.0})
        result = self.run_with({"42": 3400.0, "43": 3500.0})
        self.assertEqual([c.size_eu for c in result.changes], ["42"])

    def test_repeated_check_does_not_resend(self):
        first = self.run_with({"42": 3799.0, "43": 4599.0})
        self.assertEqual(len(first.changes), 1)
        second = self.run_with({"42": 3799.0, "43": 4599.0})
        self.assertEqual(second.changes, [])

    def test_previous_price_recorded(self):
        self.run_with({"42": 3799.0, "43": 4599.0})
        record = self.fresh_store().get("DD1503-101", "42")
        self.assertEqual(record.previous_price, 3999)
        self.assertEqual(record.current_price, 3799)


class TestNoFalsePositives(CheckerTestCase):
    def setUp(self):
        super().setUp()
        self.baseline()
        self.items = [TrackedItem("DD1503-101", "42", 2), TrackedItem("DD1503-101", "43", 1)]

    def test_size_disappeared_is_not_a_change(self):
        store = self.fresh_store()
        result = run_check(
            self.items, store, FakeSite({"DD1503-101": snapshot(prices={"43": 4599.0})})
        )
        self.assertEqual(result.changes, [])
        self.assertEqual(result.sizes_missing, 1)
        self.assertEqual(store.get("DD1503-101", "42").current_price, 3999)  # цена сохранена

    def test_product_not_found_is_ignored(self):
        store = self.fresh_store()
        result = run_check(self.items, store, FakeSite({"DD1503-101": None}))
        self.assertEqual(result.changes, [])
        self.assertEqual(result.products_missing, 1)
        self.assertEqual(store.get("DD1503-101", "42").current_price, 3999)

    def test_temporary_site_error_does_not_touch_state(self):
        store = self.fresh_store()
        site = FakeSite({"DD1503-101": ResolveResult(None, None, 1, "temporary:HTTP 503")})
        result = run_check(self.items, store, site)
        self.assertEqual(result.changes, [])
        self.assertEqual(result.errors, 1)
        self.assertEqual(store.get("DD1503-101", "42").current_price, 3999)

    def test_zero_price_is_rejected(self):
        store = self.fresh_store()
        result = run_check(
            self.items, store, FakeSite({"DD1503-101": snapshot(prices={"42": 0.0, "43": 4599.0})})
        )
        self.assertEqual(result.changes, [])
        self.assertEqual(store.get("DD1503-101", "42").current_price, 3999)

    def test_negative_and_absurd_prices_rejected(self):
        store = self.fresh_store()
        run_check(self.items, store, FakeSite({"DD1503-101": snapshot(prices={"42": -5.0})}))
        self.assertEqual(store.get("DD1503-101", "42").current_price, 3999)
        run_check(self.items, store, FakeSite({"DD1503-101": snapshot(prices={"42": 9e12})}))
        self.assertEqual(store.get("DD1503-101", "42").current_price, 3999)

    def test_exception_in_one_product_does_not_stop_others(self):
        class BrokenSite(FakeSite):
            def resolve(self, article, cached_url=None):
                if article == "BROKEN-001":
                    raise RuntimeError("boom")
                return super().resolve(article, cached_url)

        store = self.fresh_store()
        items = self.items + [TrackedItem("BROKEN-001", "44", 1)]
        result = run_check(
            items, store, BrokenSite({"DD1503-101": snapshot(prices={"42": 3799.0, "43": 4599.0})})
        )
        self.assertEqual(result.errors, 1)
        self.assertEqual([c.size_eu for c in result.changes], ["42"])

    def test_huge_discount_is_still_reported(self):
        store = self.fresh_store()
        result = run_check(
            self.items, store, FakeSite({"DD1503-101": snapshot(prices={"42": 500.0})})
        )
        self.assertEqual(len(result.changes), 1)
        self.assertTrue(result.changes[0].is_anomaly)  # логируется как аномалия, но шлётся


class TestNewItems(CheckerTestCase):
    def test_new_size_added_later_is_baseline_only(self):
        self.baseline()
        items = [
            TrackedItem("DD1503-101", "42", 2),
            TrackedItem("DD1503-101", "43", 1),
            TrackedItem("DD1503-101", "44", 1),
        ]
        store = self.fresh_store()
        result = run_check(
            items,
            store,
            FakeSite({"DD1503-101": snapshot(prices={"42": 3999.0, "43": 4599.0, "44": 5000.0})}),
        )
        self.assertEqual(result.changes, [])
        self.assertEqual(result.baseline_items, 1)
        self.assertEqual(store.get("DD1503-101", "44").current_price, 5000)


class TestMatching(CheckerTestCase):
    def test_one_request_per_article_regardless_of_sizes(self):
        items = [TrackedItem("DD1503-101", str(s), 1) for s in (42, 43, 44, 45)]
        site = FakeSite(
            {"DD1503-101": snapshot(prices={"42": 1.0, "43": 2.0, "44": 3.0, "45": 4.0})}
        )
        run_check(items, self.fresh_store(), site)
        self.assertEqual(len(site.calls), 1)

    def test_cached_url_is_passed_to_site(self):
        self.baseline()
        store = self.fresh_store()
        site = FakeSite({"DD1503-101": snapshot()})
        run_check([TrackedItem("DD1503-101", "42", 1)], store, site)
        self.assertEqual(site.calls[0][1], URL)

    def test_wrong_name_does_not_affect_matching(self):
        self.baseline()
        store = self.fresh_store()
        weird = snapshot(name="СОВЕРШЕННО ДРУГОЕ НАЗВАНИЕ", prices={"42": 3699.0})
        result = run_check([TrackedItem("DD1503-101", "42", 1)], store, FakeSite({"DD1503-101": weird}))
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].article, "DD1503-101")


if __name__ == "__main__":
    unittest.main()
