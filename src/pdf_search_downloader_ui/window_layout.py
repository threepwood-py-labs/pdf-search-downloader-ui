"""Screen-splitting helpers for the app and browser windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCursor, QGuiApplication

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class WindowBounds:
    """Describe one top-level window rectangle."""

    left: int
    top: int
    width: int
    height: int

    def to_qrect(self) -> QRect:
        """Return the bounds as a ``QRect`` instance."""

        return QRect(self.left, self.top, self.width, self.height)


@dataclass(frozen=True)
class ScreenSplitLayout:
    """Describe the left and right halves of one screen."""

    left: WindowBounds
    right: WindowBounds


_preferred_layout: ScreenSplitLayout | None = None


def split_screen_layout(screen_geometry: QRect) -> ScreenSplitLayout:
    """Split one screen geometry into left and right halves."""

    left_width = max(1, screen_geometry.width() // 2)
    right_width = max(1, screen_geometry.width() - left_width)
    left_bounds = WindowBounds(
        left=screen_geometry.x(),
        top=screen_geometry.y(),
        width=left_width,
        height=screen_geometry.height(),
    )
    right_bounds = WindowBounds(
        left=screen_geometry.x() + left_width,
        top=screen_geometry.y(),
        width=right_width,
        height=screen_geometry.height(),
    )
    return ScreenSplitLayout(left=left_bounds, right=right_bounds)


def current_split_screen_layout() -> ScreenSplitLayout:
    """Return the split layout for the screen under the cursor."""

    return _split_screen_layout_for_point(QCursor.pos())


def split_screen_layout_for_widget(widget: QWidget) -> ScreenSplitLayout:
    """Return the split layout for the screen that contains one widget."""

    return _split_screen_layout_for_point(widget.frameGeometry().center())


def preferred_split_screen_layout() -> ScreenSplitLayout:
    """Return the preferred split layout for new top-level windows."""

    if _preferred_layout is not None:
        return _preferred_layout
    return current_split_screen_layout()


def set_preferred_split_screen_layout(layout: ScreenSplitLayout) -> None:
    """Persist the preferred split layout for future browser launches."""

    global _preferred_layout
    _preferred_layout = layout


def _split_screen_layout_for_point(point: QPoint) -> ScreenSplitLayout:
    """Return the split layout for the screen that contains one point."""

    screens = QGuiApplication.screens()
    for screen in screens:
        if screen.availableGeometry().contains(point):
            return split_screen_layout(screen.availableGeometry())

    if not screens:
        return split_screen_layout(QRect(0, 0, 1280, 900))
    return split_screen_layout(screens[0].availableGeometry())
