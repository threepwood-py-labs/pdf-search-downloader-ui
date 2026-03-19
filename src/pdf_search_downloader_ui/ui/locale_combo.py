"""Reusable locale combo-box helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QWidget

from ..locale_options import COMMON_LOCALE_OPTIONS, locale_option_for_codes


def build_locale_combo(
    parent: QWidget,
    *,
    current_language: str,
    current_market: str,
) -> QComboBox:
    """Build one combo populated with common locale presets."""

    combo_box = QComboBox(parent)
    for option in COMMON_LOCALE_OPTIONS:
        combo_box.addItem(option.label, option)
    selected_option = locale_option_for_codes(current_language, current_market)
    combo_box.setCurrentText(selected_option.label)
    return combo_box
