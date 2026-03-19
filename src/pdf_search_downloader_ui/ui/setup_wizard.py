"""First-run setup wizard for PDF search downloader runtime settings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from threep_commons.qt.widget_identity import assign_widget_identity

from ..config import (
    AppConfig,
    BrowserConfig,
    DownloadConfig,
    ProviderConfig,
    SearchConfig,
)
from ..locale_options import locale_option_for_label
from ..widget_naming import control_widget_id, window_widget_id
from .locale_combo import build_locale_combo


class SetupWizardDialog(QDialog):
    """Collect the minimum runtime configuration for the app."""

    def __init__(self, initial: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._initial = initial
        self.setWindowTitle("PDF Search Downloader Setup")
        self.resize(720, 420)
        root = QVBoxLayout(self)
        intro = QLabel(
            "Configure providers, browser profile, and download defaults. "
            "The app uses a visible persistent browser session."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.chk_google = QCheckBox("Enable Google scraping")
        self.chk_google.setChecked(initial.providers.google_enabled)
        self.chk_bing = QCheckBox("Enable Bing scraping")
        self.chk_bing.setChecked(initial.providers.bing_enabled)

        self.cmb_locale = build_locale_combo(
            self,
            current_language=initial.search.default_language,
            current_market=initial.search.default_market,
        )
        self.spn_pages = QSpinBox()
        self.spn_pages.setRange(1, 10)
        self.spn_pages.setValue(initial.search.max_pages)
        self.spn_results = QSpinBox()
        self.spn_results.setRange(1, 200)
        self.spn_results.setValue(initial.search.max_results)
        self.chk_skip_duplicates = QCheckBox("Skip duplicates using manifest")
        self.chk_skip_duplicates.setChecked(initial.downloads.skip_duplicates)
        self.spn_download_timeout = QSpinBox()
        self.spn_download_timeout.setRange(5, 120)
        self.spn_download_timeout.setSuffix(" sec")
        self.spn_download_timeout.setValue(initial.downloads.timeout_seconds)

        self.txt_output_dir = QLineEdit(str(initial.downloads.output_dir))
        self.btn_browse_output = QPushButton("Browse...")
        self.btn_browse_output.clicked.connect(self._choose_output_dir)
        output_row = QHBoxLayout()
        output_row.addWidget(self.txt_output_dir)
        output_row.addWidget(self.btn_browse_output)

        self.txt_profile_dir = QLineEdit(str(initial.browser.profile_dir))
        self.btn_browse_profile = QPushButton("Browse...")
        self.btn_browse_profile.clicked.connect(self._choose_profile_dir)
        profile_row = QHBoxLayout()
        profile_row.addWidget(self.txt_profile_dir)
        profile_row.addWidget(self.btn_browse_profile)

        form.addRow("Google", self.chk_google)
        form.addRow("Bing", self.chk_bing)
        form.addRow("Default locale", self.cmb_locale)
        form.addRow("Max pages", self.spn_pages)
        form.addRow("Max results", self.spn_results)
        form.addRow("Download timeout", self.spn_download_timeout)
        form.addRow("Output directory", output_row)
        form.addRow("Browser profile directory", profile_row)
        form.addRow("Duplicates", self.chk_skip_duplicates)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        assign_widget_identity(
            self,
            widget_id=window_widget_id("setup_wizard"),
            widget_alias="window.setup_wizard",
        )
        assign_widget_identity(
            self.txt_output_dir,
            widget_id=control_widget_id("setup_wizard", "output_dir"),
            widget_alias="setup.output_dir",
        )
        assign_widget_identity(
            self.txt_profile_dir,
            widget_id=control_widget_id("setup_wizard", "profile_dir"),
            widget_alias="setup.profile_dir",
        )

    def _choose_directory(self, line_edit: QLineEdit) -> None:
        """Choose one directory and write it into the supplied control."""

        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose directory",
            line_edit.text(),
        )
        if chosen:
            line_edit.setText(chosen)

    def _choose_output_dir(self) -> None:
        """Choose the output directory."""

        self._choose_directory(self.txt_output_dir)

    def _choose_profile_dir(self) -> None:
        """Choose the browser profile directory."""

        self._choose_directory(self.txt_profile_dir)

    def _on_accept(self) -> None:
        """Validate the wizard fields before accepting."""

        if not self.chk_google.isChecked() and not self.chk_bing.isChecked():
            QMessageBox.warning(
                self,
                "Validation",
                "Enable at least one provider before saving.",
            )
            return
        if not self.txt_output_dir.text().strip():
            QMessageBox.warning(
                self,
                "Validation",
                "Output directory cannot be empty.",
            )
            return
        if not self.txt_profile_dir.text().strip():
            QMessageBox.warning(
                self,
                "Validation",
                "Browser profile directory cannot be empty.",
            )
            return
        self.accept()

    def to_config(self) -> AppConfig:
        """Convert the current dialog state into one persisted config."""

        locale_option = locale_option_for_label(self.cmb_locale.currentText())
        return replace(
            self._initial,
            providers=ProviderConfig(
                google_enabled=self.chk_google.isChecked(),
                bing_enabled=self.chk_bing.isChecked(),
            ),
            search=SearchConfig(
                default_language=locale_option.language,
                default_market=locale_option.market,
                max_pages=int(self.spn_pages.value()),
                max_results=int(self.spn_results.value()),
            ),
            downloads=DownloadConfig(
                output_dir=Path(self.txt_output_dir.text().strip()),
                skip_duplicates=self.chk_skip_duplicates.isChecked(),
                timeout_seconds=int(self.spn_download_timeout.value()),
            ),
            browser=BrowserConfig(
                profile_dir=Path(self.txt_profile_dir.text().strip())
            ),
            setup_completed=True,
        )


def run_setup_wizard(
    initial: AppConfig,
    parent: QWidget | None = None,
) -> AppConfig | None:
    """Run the setup wizard and return the accepted config."""

    dialog = SetupWizardDialog(initial, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.to_config()
