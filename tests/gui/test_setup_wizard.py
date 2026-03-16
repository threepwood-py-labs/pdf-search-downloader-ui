from __future__ import annotations

from pathlib import Path

from pdf_search_downloader_ui.config import get_default_config
from pdf_search_downloader_ui.ui.setup_wizard import SetupWizardDialog


def test_setup_wizard_validates_provider_selection(qtbot, monkeypatch) -> None:
    dialog = SetupWizardDialog(get_default_config())
    qtbot.addWidget(dialog)
    dialog.chk_google.setChecked(False)
    dialog.chk_bing.setChecked(False)

    captured: list[str] = []
    monkeypatch.setattr(
        "pdf_search_downloader_ui.ui.setup_wizard.QMessageBox.warning",
        lambda *_args: captured.append("warning"),
    )

    dialog._on_accept()

    assert captured == ["warning"]


def test_setup_wizard_to_config_sets_setup_completed(qtbot) -> None:
    dialog = SetupWizardDialog(get_default_config())
    qtbot.addWidget(dialog)
    dialog.txt_output_dir.setText("C:/tmp/downloads")
    dialog.txt_profile_dir.setText("C:/tmp/profile")

    config = dialog.to_config()

    assert config.setup_completed is True
    assert config.downloads.output_dir == Path("C:/tmp/downloads")
    assert config.browser.profile_dir == Path("C:/tmp/profile")
