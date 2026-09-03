"""Тесты формирования Telegram-сообщений."""

import unittest

from src import config
from src.checker import PriceChange
from src.notifier import NBSP, build_messages, format_change_block, format_price


def change(article="DD1503-101", size="42", old=3999.0, new=3699.0, name="Nike Dunk Low Panda"):
    return PriceChange(
        article=article,
        size_eu=size,
        product_name=name,
        product_url=f"https://yesoriginal.com.ua/uk/x-{article.lower()}",
        old_price=old,
        new_price=new,
    )


class TestFormatting(unittest.TestCase):
    def test_price_formatting(self):
        self.assertEqual(format_price(3999), "3" + NBSP + "999")
        self.assertEqual(format_price(500), "500")

    def test_decrease_block(self):
        text = format_change_block(change())
        self.assertIn("📉", text)
        self.assertIn("Цена снизилась", text)
        self.assertIn("DD1503-101", text)
        self.assertIn("Размер: EU 42", text)
        self.assertIn("−300", text)
        self.assertIn("7.50%", text)
        self.assertIn('<a href="https://yesoriginal.com.ua/uk/x-dd1503-101">', text)

    def test_increase_block(self):
        text = format_change_block(change(old=4999.0, new=5499.0))
        self.assertIn("📈", text)
        self.assertIn("Цена выросла", text)
        self.assertIn("+500", text)
        self.assertIn("+10.00%", text)

    def test_html_is_escaped(self):
        text = format_change_block(change(name="Nike <b>&</b> Co"))
        self.assertIn("Nike &lt;b&gt;&amp;&lt;/b&gt; Co", text)


class TestMessages(unittest.TestCase):
    def test_no_changes_no_messages(self):
        self.assertEqual(build_messages([]), [])

    def test_several_changes_in_one_message(self):
        messages = build_messages([change(), change(article="HF9303-045", size="44",
                                                    old=4999.0, new=5499.0)])
        self.assertEqual(len(messages), 1)
        self.assertIn("Всего изменений: <b>2</b>", messages[0])
        self.assertIn("DD1503-101", messages[0])
        self.assertIn("HF9303-045", messages[0])

    def test_decreases_come_first(self):
        text = build_messages([
            change(article="AAA-1", old=100.0, new=200.0),   # рост
            change(article="BBB-2", old=200.0, new=100.0),   # падение
        ])[0]
        self.assertLess(text.index("BBB-2"), text.index("AAA-1"))

    def test_long_list_is_split(self):
        changes = [change(article=f"ART-{i:03d}", size=str(40 + i % 8)) for i in range(80)]
        messages = build_messages(changes)
        self.assertGreater(len(messages), 1)
        for text in messages:
            self.assertLessEqual(len(text), config.TELEGRAM_MAX_LEN)
        self.assertIn("часть 1/", messages[0])
        self.assertIn("Всего изменений: <b>80</b>", messages[-1])
        # ни одно изменение не потеряно
        joined = "".join(messages)
        for i in range(80):
            self.assertIn(f"ART-{i:03d}", joined)

    def test_single_message_has_no_part_marker(self):
        self.assertNotIn("часть", build_messages([change()])[0])


if __name__ == "__main__":
    unittest.main()
