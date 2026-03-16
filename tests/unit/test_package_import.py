"""Tests for pdf_search_downloader_ui."""

import importlib


def test_package_importable() -> None:
    assert importlib.import_module("pdf_search_downloader_ui")
