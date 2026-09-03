"""Тесты поиска товара по артикулу (сопоставление ТОЛЬКО по артикулу)."""

import pathlib
import unittest

from src.site import PermanentFetchError, Site, TemporaryFetchError

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

DR_URL = "https://yesoriginal.com.ua/uk/krossovki-muzhskie-nike-court-vision-mid-grey-dr7882-003"
CU_URL = "https://yesoriginal.com.ua/uk/krossovki-muzhskie-nike-court-vision-mid-black-cu6620-001"


def load(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, text, url):
        self.text = text
        self.url = url
        self.status_code = 200


class FakeSite(Site):
    """Site с подменённым транспортом: HTTP не выполняется."""

    def __init__(self, routes, failures=None):
        super().__init__()
        self.routes = routes            # url -> (text, final_url)
        self.failures = failures or {}  # url -> исключение
        self.fetched = []

    def get(self, url):
        self.fetched.append(url)
        self.request_count += 1
        if url in self.failures:
            raise self.failures[url]
        if url not in self.routes:
            raise PermanentFetchError(f"404 для {url}")
        text, final = self.routes[url]
        return FakeResponse(text, final)


class TestResolve(unittest.TestCase):
    def test_exact_article_search_redirects_to_product(self):
        site_search = (
            "https://yesoriginal.com.ua/index.php?route=product/search&search=DR7882-003"
        )
        site = FakeSite({site_search: (load("product_dr7882-003.html"), DR_URL)})
        result = site.resolve("DR7882-003")
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.snapshot.article, "DR7882-003")
        self.assertEqual(result.url, DR_URL)
        self.assertEqual(result.reason, "search-redirect")
        self.assertEqual(site.fetched, [site_search])

    def test_cached_url_costs_one_request(self):
        site = FakeSite({DR_URL: (load("product_dr7882-003.html"), DR_URL)})
        result = site.resolve("DR7882-003", cached_url=DR_URL)
        self.assertEqual(result.reason, "cached-url")
        self.assertEqual(len(site.fetched), 1)

    def test_cached_url_with_wrong_article_falls_back_to_search(self):
        site = FakeSite({
            DR_URL: (load("product_fj1966-133.html"), DR_URL),  # там другой артикул
            "https://yesoriginal.com.ua/index.php?route=product/search&search=DR7882-003":
                (load("product_dr7882-003.html"), DR_URL),
        })
        result = site.resolve("DR7882-003", cached_url=DR_URL)
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.snapshot.article, "DR7882-003")
        self.assertEqual(len(site.fetched), 2)

    def test_not_found_returns_nothing(self):
        site = FakeSite({
            "https://yesoriginal.com.ua/index.php?route=product/search&search=NOPE-999":
                (load("search_no_results.html"), "https://yesoriginal.com.ua/uk/search?search=NOPE-999"),
        })
        result = site.resolve("NOPE-999")
        self.assertIsNone(result.snapshot)
        self.assertEqual(result.reason, "not-found")

    def test_search_candidates_are_checked_by_article(self):
        site = FakeSite({
            "https://yesoriginal.com.ua/index.php?route=product/search&search=DR7882-003":
                (load("search_results.html"), "https://yesoriginal.com.ua/uk/search?search=DR7882-003"),
            DR_URL: (load("product_dr7882-003.html"), DR_URL),
            CU_URL: (load("product_fj1966-133.html"), CU_URL),
        })
        result = site.resolve("DR7882-003")
        self.assertIsNotNone(result.snapshot)
        self.assertEqual(result.snapshot.article, "DR7882-003")
        self.assertEqual(result.reason, "search-candidate")
        # Кандидат с подходящим slug проверяется первым
        self.assertEqual(site.fetched[1], DR_URL)

    def test_temporary_error_on_cached_url_does_not_trigger_search(self):
        site = FakeSite(
            {}, failures={DR_URL: TemporaryFetchError("HTTP 503")}
        )
        result = site.resolve("DR7882-003", cached_url=DR_URL)
        self.assertIsNone(result.snapshot)
        self.assertTrue(result.reason.startswith("temporary"))
        self.assertEqual(len(site.fetched), 1)  # в поиск не пошли — состояние не трогаем

    def test_article_case_insensitive_match(self):
        site = FakeSite({
            "https://yesoriginal.com.ua/index.php?route=product/search&search=dr7882-003":
                (load("product_dr7882-003.html"), DR_URL),
        })
        self.assertIsNotNone(site.resolve("dr7882-003").snapshot)

    def test_article_with_space_is_url_encoded(self):
        site = FakeSite({})
        self.assertIn("50467556%20002", site.search_url("50467556 002"))


if __name__ == "__main__":
    unittest.main()
