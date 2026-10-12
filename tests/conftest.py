"""Shared pytest configuration and fixtures for GUI test suites."""

import os
from collections.abc import Generator
import pytest
from PySide6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session")
def qapp() -> Generator[QApplication, None, None]:
    """Provide a singleton QApplication for offscreen widget testing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["-platform", "offscreen"])
    yield app
