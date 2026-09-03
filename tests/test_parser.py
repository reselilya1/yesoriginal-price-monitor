"""Тесты разбора страницы товара (на реальной вёрстке yesoriginal.com.ua)."""

import pathlib
import unittest

from src.parser import extract_search_result_links, parse_product_page

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class TestProductParsing(unittest.TestCase):
    def test_article_taken_from_json_ld_model(self):
        snap = parse_product_page(load("product_dr7882-003.html"))
        self.assertIsNotNone(snap)
        self.assertEqual(snap.article, "DR7882-003")
        self.assertIn("Court Vision", snap.name)
        self.assertTrue(snap.url.startswith("https://yesoriginal.com.ua/uk/"))

    def test_price_differs_per_size(self):
        snap = parse_product_page(load("product_dr7882-003.html"))
        self.assertEqual(snap.price_for("42").price, 2990)
        self.assertEqual(snap.price_for("43").price, 4090)
        self.assertEqual(snap.price_for("44").price, 3990)
        self.assertEqual(snap.price_for("44.5").price, 5390)

    def test_final_price_is_special_not_base(self):
        snap = parse_product_page(load("product_dr7882-003.html"))
        offer = snap.price_for("42")
        self.assertEqual(offer.price, 2990)       # то, что реально платит покупатель
        self.assertEqual(offer.base_price, 5990)  # зачёркнутая — для мониторинга не нужна
        self.assertTrue(offer.has_discount)

    def test_special_zero_means_no_discount(self):
        snap = parse_product_page(load("product_7-37cma0018312.html"))
        self.assertEqual(snap.article, "7-37CMA0018312")
        self.assertEqual(snap.price_for("44.5").price, 1690)
        self.assertEqual(snap.price_for("46").price, 1590)
        self.assertFalse(snap.price_for("46").has_discount)

    def test_size_normalization_on_lookup(self):
        snap = parse_product_page(load("product_dr7882-003.html"))
        self.assertIsNotNone(snap.price_for("44,5"))
        self.assertIsNotNone(snap.price_for("42.0"))

    def test_missing_size_returns_none(self):
        snap = parse_product_page(load("product_dr7882-003.html"))
        self.assertIsNone(snap.price_for("47"))
        self.assertIsNone(snap.price_for("XXL"))

    def test_two_availability_sections_first_wins(self):
        snap = parse_product_page(load("product_db4109-001.html"))
        # 44 есть в обоих блоках: 6990 (доставка 1-2 дня) и 6890 (из EU)
        self.assertEqual(snap.price_for("44").price, 6990)
        self.assertIn("1-2", snap.price_for("44").section_label)
        self.assertIn("44", snap.duplicate_sizes)

    def test_single_size_clothing(self):
        snap = parse_product_page(load("product_fj1966-133.html"))
        self.assertEqual(snap.sizes, ["S"])
        self.assertEqual(snap.price_for("S").price, 3690)
        self.assertEqual(snap.price_for("s").price, 3690)

    def test_json_ld_offer_price_is_not_used_blindly(self):
        # В JSON-LD цена предвыбранного (самого дешёвого) размера — 2990.
        # Для размера 43 она другая, и парсер обязан это различать.
        snap = parse_product_page(load("product_dr7882-003.html"))
        self.assertNotEqual(snap.price_for("43").price, 2990)

    def test_search_page_is_not_a_product(self):
        self.assertIsNone(parse_product_page(load("search_no_results.html")))
        self.assertIsNone(parse_product_page(load("search_results.html")))

    def test_garbage_input(self):
        self.assertIsNone(parse_product_page(""))
        self.assertIsNone(parse_product_page("<html><body>oops</body></html>"))
        self.assertIsNone(parse_product_page("x" * 400))

    def test_truncated_page_without_options(self):
        html = load("product_dr7882-003.html")
        cut = html.split('<div class="mb-4">')[0] + "</body></html>"
        snap = parse_product_page(cut)
        # Товар опознан, но размеров нет — значит цен нет, и это НЕ ноль.
        self.assertIsNotNone(snap)
        self.assertEqual(snap.offers, {})
        self.assertIsNone(snap.price_for("42"))


class TestSearchResults(unittest.TestCase):
    def test_extract_links(self):
        links = extract_search_result_links(load("search_results.html"))
        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].endswith("cu6620-001"))

    def test_no_results(self):
        self.assertEqual(extract_search_result_links(load("search_no_results.html")), [])


if __name__ == "__main__":
    unittest.main()
