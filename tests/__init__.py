"""Тесты. Логи приглушены, чтобы вывод оставался читаемым."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.WARNING)
