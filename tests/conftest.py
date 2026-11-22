"""Shared pytest configuration and fixtures for GUI test suites."""

import importlib
import os
import sys
from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from semsearch.gui.theme import set_active_theme
from semsearch.i18n import set_language

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["LANG"] = "en_US.UTF-8"
os.environ["LC_ALL"] = "en_US.UTF-8"

try:
    importlib.import_module("pynput.keyboard")
except ImportError:
    mock_pynput = MagicMock()
    mock_pynput.keyboard.HotKey.parse = staticmethod(lambda s: s.split("+"))
    sys.modules["pynput"] = mock_pynput
    sys.modules["pynput.keyboard"] = mock_pynput.keyboard


@pytest.fixture(autouse=True)
def _reset_default_language() -> Generator[None, None, None]:
    """Reset active language and theme to defaults before and after each test."""
    set_language("en")
    set_active_theme("dark")
    yield
    set_language("en")
    set_active_theme("dark")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    """Provide a singleton QApplication for offscreen widget testing."""
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication(["-platform", "offscreen"])
