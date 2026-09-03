"""Тесты чтения Google Sheets."""

import unittest

from src.sheets import (
    TrackedItem,
    group_by_article,
    normalize_article,
    normalize_size,
    parse_quantity,
    parse_sheet_csv,
)

HEADER = "Артикул,Назва,Ціна,Кількість,Розмір US,Розмір EU,Стать,Бренд"

CSV = "\n".join(
    [
        HEADER,
        "DD1503-101,Nike Dunk Low Panda,3 999,2,8.5,42,Чоловіча,Nike",
        "DD1503-101,Nike Dunk Low Panda,3 999,1,9.5,43,Чоловіча,Nike",
        "HF9303-045,Jordan,4 999,1,10,44,Чоловіча,Air Jordan",
        "HF9303-045,Jordan,4 999,0,11,45,Чоловіча,Air Jordan",  # Кількість = 0
        "",                                                      # пустая строка
        ",,,,,,,",                                               # строка из запятых
        " DX1487-016 ,Шорти,1 350,1,L,L,Чоловіча,Air Jordan",     # пробелы в артикуле
        "DD1503-101,Nike Dunk Low Panda,3 999,2,8.5,42,Чоловіча,Nike",  # дубликат
        "SX7666-010,Шкарпетки,648,13,42-46,42-46,Чоловіча,Nike",  # диапазонный размер
        "DM1106-007,Nike Winflo,3 800,1,10.5,\"44,5\",Чоловіча,Nike",  # запятая в размере
        "NOSIZE-001,Без розміру,100,3,,,Чоловіча,Nike",           # нет размера
    ]
)


class TestNormalization(unittest.TestCase):
    def test_article_trims_but_keeps_meaning(self):
        self.assertEqual(normalize_article(" DX1487-016 "), "DX1487-016")
        self.assertEqual(normalize_article("50467556  002"), "50467556 002")
        self.assertEqual(normalize_article("DD1503-101"), "DD1503-101")
        self.assertEqual(normalize_article(None), "")

    def test_size_normalization(self):
        self.assertEqual(normalize_size("44,5"), "44.5")
        self.assertEqual(normalize_size("42.0"), "42")
        self.assertEqual(normalize_size(" m "), "M")
        self.assertEqual(normalize_size("42-46"), "42-46")
        self.assertEqual(normalize_size(""), "")

    def test_quantity(self):
        self.assertEqual(parse_quantity("2"), 2)
        self.assertEqual(parse_quantity(""), 0)
        self.assertEqual(parse_quantity("0"), 0)
        self.assertEqual(parse_quantity(" 1 шт"), 1)
        self.assertEqual(parse_quantity("мусор"), 0)


class TestParseSheet(unittest.TestCase):
    def setUp(self):
        self.items = parse_sheet_csv(CSV)

    def test_only_positive_quantity(self):
        keys = {(i.article, i.size_eu) for i in self.items}
        self.assertIn(("HF9303-045", "44"), keys)
        self.assertNotIn(("HF9303-045", "45"), keys)  # Кількість = 0

    def test_multiple_sizes_of_one_article(self):
        keys = {(i.article, i.size_eu) for i in self.items}
        self.assertIn(("DD1503-101", "42"), keys)
        self.assertIn(("DD1503-101", "43"), keys)

    def test_duplicates_removed(self):
        pairs = [(i.article, i.size_eu) for i in self.items]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_article_whitespace_stripped(self):
        articles = {i.article for i in self.items}
        self.assertIn("DX1487-016", articles)
        self.assertNotIn(" DX1487-016 ", articles)

    def test_size_formats(self):
        keys = {(i.article, i.size_eu) for i in self.items}
        self.assertIn(("SX7666-010", "42-46"), keys)
        self.assertIn(("DM1106-007", "44.5"), keys)

    def test_rows_without_size_skipped(self):
        self.assertNotIn("NOSIZE-001", {i.article for i in self.items})

    def test_empty_rows_skipped(self):
        self.assertTrue(all(i.article and i.size_eu for i in self.items))

    def test_columns_found_by_header_not_index(self):
        shuffled = "\n".join(
            [
                "Бренд,Розмір EU,Назва,Кількість,Артикул",
                "Nike,42,Nike Dunk,2,DD1503-101",
                "Nike,43,Nike Dunk,0,DD1503-101",
            ]
        )
        items = parse_sheet_csv(shuffled)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].article, "DD1503-101")
        self.assertEqual(items[0].size_eu, "42")

    def test_header_not_in_first_row(self):
        with_preamble = "Мій склад,,,\n\n" + CSV
        items = parse_sheet_csv(with_preamble)
        self.assertTrue(items)

    def test_missing_columns_raise(self):
        with self.assertRaises(ValueError):
            parse_sheet_csv("A,B,C\n1,2,3")


class TestGrouping(unittest.TestCase):
    def test_group_by_article(self):
        items = [
            TrackedItem("DD1503-101", "42", 2),
            TrackedItem("DD1503-101", "43", 1),
            TrackedItem("HF9303-045", "44", 1),
        ]
        grouped = group_by_article(items)
        self.assertEqual(len(grouped), 2)
        self.assertEqual([i.size_eu for i in grouped["DD1503-101"]], ["42", "43"])


if __name__ == "__main__":
    unittest.main()
