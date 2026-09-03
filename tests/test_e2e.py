"""Сквозной сценарий: CSV -> поиск -> парсинг -> сравнение -> текст уведомления.

HTTP не выполняется: транспорт подменён, но HTML — настоящая вёрстка сайта.
"""

import pathlib
import re
import tempfile
import unittest

from src.checker import run_check
from src.notifier import build_messages
from src.sheets import parse_sheet_csv
from src.store import Store
from tests.test_site import FakeSite, load

DR_URL = "https://yesoriginal.com.ua/uk/krossovki-muzhskie-nike-court-vision-mid-grey-dr7882-003"
FJ_URL = "https://yesoriginal.com.ua/uk/khudi-air-jordan-air-wordmark-white-fj1966-133"
SEARCH = "https://yesoriginal.com.ua/index.php?route=product/search&search={}"

CSV = "\n".join([
    "Артикул,Назва,Ціна,Кількість,Розмір US,Розмір EU,Стать,Бренд",
    "DR7882-003,Nike Court Vision Mid,2 800,2,8.5,42,Чоловіча,Nike",
    "DR7882-003,Nike Court Vision Mid,2 800,2,9.5,43,Чоловіча,Nike",
    "DR7882-003,Nike Court Vision Mid,2 800,0,10,44,Чоловіча,Nike",     # Кількість = 0
    "FJ1966-133,Худі Jordan,2 500,1,S,S,Чоловіча,Air Jordan",
    "NOPE-999,Неіснуючий товар,100,1,42,42,Чоловіча,Nike",              # нет на сайте
])


def routes(dr_html=None):
    dr_html = dr_html or load("product_dr7882-003.html")
    return {
        SEARCH.format("DR7882-003"): (dr_html, DR_URL),
        SEARCH.format("FJ1966-133"): (load("product_fj1966-133.html"), FJ_URL),
        SEARCH.format("NOPE-999"): (
            load("search_no_results.html"),
            "https://yesoriginal.com.ua/uk/search?search=NOPE-999",
        ),
        DR_URL: (dr_html, DR_URL),
        FJ_URL: (load("product_fj1966-133.html"), FJ_URL),
    }


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "state.json"
        self.items = parse_sheet_csv(CSV)

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, dr_html=None):
        store = Store.load(self.path)
        result = run_check(self.items, store, FakeSite(routes(dr_html)))
        store.save()
        return result, store

    def test_sheet_filtering(self):
        pairs = {(i.article, i.size_eu) for i in self.items}
        self.assertEqual(
            pairs,
            {("DR7882-003", "42"), ("DR7882-003", "43"),
             ("FJ1966-133", "S"), ("NOPE-999", "42")},
        )

    def test_baseline_run(self):
        result, store = self.run_once()
        self.assertTrue(result.baseline)
        self.assertEqual(result.changes, [])
        self.assertEqual(result.products_missing, 1)          # NOPE-999 просто проигнорирован
        self.assertEqual(store.get("DR7882-003", "42").current_price, 2990)
        self.assertEqual(store.get("DR7882-003", "43").current_price, 4090)
        self.assertEqual(store.get("FJ1966-133", "S").current_price, 3690)
        self.assertIsNone(store.get("DR7882-003", "44"))      # Кількість = 0 не отслеживается

    def test_second_run_without_changes(self):
        self.run_once()
        result, _ = self.run_once()
        self.assertFalse(result.baseline)
        self.assertEqual(result.changes, [])
        self.assertEqual(build_messages(result.changes), [])

    def test_price_change_produces_message(self):
        self.run_once()
        changed = load("product_dr7882-003.html").replace(
            'data-price="5990" data-special="2990"', 'data-price="5990" data-special="2690"'
        )
        result, store = self.run_once(changed)
        self.assertEqual(len(result.changes), 1)
        change = result.changes[0]
        self.assertEqual(change.size_eu, "42")
        self.assertEqual((change.old_price, change.new_price), (2990, 2690))

        text = build_messages(result.changes)[0]
        self.assertIn("Изменение цен на YesOriginal", text)
        self.assertIn("📉", text)
        self.assertIn("DR7882-003", text)
        self.assertIn("Размер: EU 42", text)
        self.assertIn("−300", text)
        self.assertIn("−10.03%", text)
        self.assertIn(DR_URL, text)
        self.assertIn("Всего изменений: <b>1</b>", text)

        # Повторный /check то же изменение не присылает
        again, _ = self.run_once(changed)
        self.assertEqual(again.changes, [])
        self.assertEqual(store.get("DR7882-003", "42").previous_price, 2990)

    def test_url_cache_saves_requests(self):
        _, store = self.run_once()
        self.assertEqual(store.get_url("DR7882-003"), DR_URL)
        store2 = Store.load(self.path)
        site = FakeSite(routes())
        run_check(self.items, store2, site)
        # DR и FJ берутся по прямому URL, NOPE-999 каждый раз идёт в поиск
        self.assertIn(DR_URL, site.fetched)
        self.assertIn(FJ_URL, site.fetched)
        self.assertNotIn(SEARCH.format("DR7882-003"), site.fetched)

    def test_broken_page_does_not_wipe_prices(self):
        self.run_once()
        broken = "<html><body>502 Bad Gateway</body></html>" + "x" * 600
        result, store = self.run_once(broken)
        self.assertEqual(result.changes, [])
        self.assertEqual(store.get("DR7882-003", "42").current_price, 2990)

    def test_page_without_sizes_does_not_wipe_prices(self):
        self.run_once()
        html = load("product_dr7882-003.html")
        stripped = re.sub(r'<input type="radio".*?</label>', "", html, flags=re.S)
        result, store = self.run_once(stripped)
        self.assertEqual(result.changes, [])
        self.assertEqual(result.sizes_missing, 2)
        self.assertEqual(store.get("DR7882-003", "42").current_price, 2990)

    def test_state_file_contains_required_fields(self):
        self.run_once()
        store = Store.load(self.path)
        record = store.get("DR7882-003", "42")
        for attribute in ("article", "size_eu", "current_price", "previous_price",
                          "product_url", "product_name", "first_seen_at", "last_checked_at"):
            self.assertTrue(hasattr(record, attribute))
        self.assertEqual(record.article, "DR7882-003")
        self.assertEqual(record.size_eu, "42")
        self.assertTrue(record.product_url.startswith("https://"))
        self.assertIn("Court Vision", record.product_name)


if __name__ == "__main__":
    unittest.main()
