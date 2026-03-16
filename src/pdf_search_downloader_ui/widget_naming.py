"""Stable widget naming helpers for the PDF search downloader UI."""

from __future__ import annotations


def window_widget_id(window_id: str) -> str:
    """Build the widget id used by the main window."""

    return f"window:{window_id.strip() or 'main'}"


def control_widget_id(window_id: str, control_name: str) -> str:
    """Build the widget id used by one top-level control."""

    return f"{window_widget_id(window_id)}:control:{control_name.strip()}"


def table_widget_id(window_id: str, table_name: str) -> str:
    """Build the widget id used by one table view."""

    return f"{window_widget_id(window_id)}:table:{table_name.strip()}"
