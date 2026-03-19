from __future__ import annotations

from PySide6.QtCore import QRect

import pdf_search_downloader_ui.window_layout as window_layout
from pdf_search_downloader_ui.window_layout import (
    ScreenSplitLayout,
    WindowBounds,
    preferred_split_screen_layout,
    set_preferred_split_screen_layout,
    split_screen_layout,
)


def test_split_screen_layout_divides_screen_evenly() -> None:
    layout = split_screen_layout(QRect(100, 40, 1920, 1040))

    assert layout.left == WindowBounds(left=100, top=40, width=960, height=1040)
    assert layout.right == WindowBounds(left=1060, top=40, width=960, height=1040)


def test_split_screen_layout_assigns_extra_pixel_to_right_half() -> None:
    layout = split_screen_layout(QRect(10, 20, 1919, 900))

    assert layout.left == WindowBounds(left=10, top=20, width=959, height=900)
    assert layout.right == WindowBounds(left=969, top=20, width=960, height=900)


def test_preferred_split_screen_layout_returns_explicit_override(monkeypatch) -> None:
    monkeypatch.setattr(window_layout, "_preferred_layout", None)
    explicit_layout = ScreenSplitLayout(
        left=WindowBounds(left=0, top=0, width=800, height=900),
        right=WindowBounds(left=800, top=0, width=800, height=900),
    )

    set_preferred_split_screen_layout(explicit_layout)

    assert preferred_split_screen_layout() == explicit_layout
