"""Тесты чтения настроек из окружения.

Главный случай: в GitHub Actions `${{ vars.FOO }}` для несозданной переменной
подставляется ПУСТАЯ СТРОКА. Настройки обязаны это пережить и взять значение
по умолчанию, иначе бот падает ещё на импорте.
"""

import importlib
import os
import unittest
from zoneinfo import ZoneInfo

from src import config

VARS = [
    "CHECK_HOUR", "TIMEZONE", "GOOGLE_SHEET_ID", "GOOGLE_SHEET_GID",
    "REQUEST_TIMEOUT", "REQUEST_DELAY", "MAX_RETRIES", "RETRY_BACKOFF",
    "ANOMALY_PCT", "MAX_SEARCH_CANDIDATES", "ENFORCE_SCHEDULE",
    "SITE_BASE_URL", "USER_AGENT", "STATE_PATH", "TELEGRAM_MAX_LEN",
]


class EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in VARS}

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        importlib.reload(config)

    def reload_with(self, **values):
        for name, value in values.items():
            os.environ[name] = value
        return importlib.reload(config)


class TestHelpers(EnvCase):
    def test_blank_string_falls_back_to_default(self):
        os.environ["CHECK_HOUR"] = ""
        self.assertEqual(config.env_str("CHECK_HOUR", "8"), "8")
        self.assertEqual(config.env_int("CHECK_HOUR", 8), 8)

    def test_whitespace_only_is_blank(self):
        os.environ["GOOGLE_SHEET_ID"] = "   "
        self.assertEqual(config.env_str("GOOGLE_SHEET_ID", "fallback"), "fallback")

    def test_value_is_trimmed(self):
        os.environ["GOOGLE_SHEET_ID"] = "  abc123  "
        self.assertEqual(config.env_str("GOOGLE_SHEET_ID", "x"), "abc123")

    def test_garbage_number_falls_back(self):
        os.environ["MAX_RETRIES"] = "три"
        self.assertEqual(config.env_int("MAX_RETRIES", 3), 3)
        os.environ["ANOMALY_PCT"] = "много"
        self.assertEqual(config.env_float("ANOMALY_PCT", 80.0), 80.0)

    def test_unknown_timezone_falls_back(self):
        os.environ["TIMEZONE"] = "Europe/Neverland"
        self.assertEqual(config.env_tz("TIMEZONE", "Europe/Prague"), ZoneInfo("Europe/Prague"))

    def test_bool_parsing(self):
        for text, expected in [("true", True), ("True", True), ("1", True),
                               ("yes", True), ("false", False), ("0", False), ("", False)]:
            os.environ["ENFORCE_SCHEDULE"] = text
            self.assertIs(config.env_bool("ENFORCE_SCHEDULE", False), expected, text)


class TestAllBlank(EnvCase):
    """Ровно то, что случилось на GitHub: переменные объявлены, но пустые."""

    def test_module_imports_with_every_var_blank(self):
        reloaded = self.reload_with(**{name: "" for name in VARS})
        self.assertEqual(reloaded.CHECK_HOUR, 8)
        self.assertEqual(reloaded.TIMEZONE, ZoneInfo("Europe/Prague"))
        self.assertEqual(reloaded.GOOGLE_SHEET_ID, "18G299yWL8DWkal7Ty_XFmzmI0ZWp4mBKFZonWpkBRvE")
        self.assertEqual(reloaded.GOOGLE_SHEET_GID, "1297484631")
        self.assertEqual(reloaded.MAX_RETRIES, 3)
        self.assertEqual(reloaded.ANOMALY_PCT, 80.0)
        self.assertFalse(reloaded.ENFORCE_SCHEDULE)
        self.assertEqual(reloaded.BASE_URL, "https://yesoriginal.com.ua")
        self.assertTrue(str(reloaded.STATE_PATH).endswith("state.json"))

    def test_sheet_url_is_valid_when_vars_blank(self):
        reloaded = self.reload_with(GOOGLE_SHEET_ID="", GOOGLE_SHEET_GID="")
        from src.sheets import build_csv_url

        url = build_csv_url(reloaded.GOOGLE_SHEET_ID, reloaded.GOOGLE_SHEET_GID)
        self.assertNotIn("/d//", url)
        self.assertIn("18G299yWL8DWkal7Ty_XFmzmI0ZWp4mBKFZonWpkBRvE", url)

    def test_real_values_still_win(self):
        reloaded = self.reload_with(CHECK_HOUR="20", TIMEZONE="Europe/Kyiv", GOOGLE_SHEET_GID="42")
        self.assertEqual(reloaded.CHECK_HOUR, 20)
        self.assertEqual(reloaded.TIMEZONE, ZoneInfo("Europe/Kyiv"))
        self.assertEqual(reloaded.GOOGLE_SHEET_GID, "42")


if __name__ == "__main__":
    unittest.main()
