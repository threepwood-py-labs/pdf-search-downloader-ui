"""Main application window for the PDF search downloader UI."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QSettings, QUrl
from PySide6.QtGui import QAction, QBrush, QCloseEvent, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from threep_commons.qt.widget_identity import assign_widget_identity

from ..config import (
    AppConfig,
    config_store_file_path,
    last_query,
    save_config,
    set_last_query,
)
from ..constants import APP_DISPLAY_NAME
from ..locale_options import locale_option_for_codes, locale_option_for_label
from ..models import DownloadOutcome, DownloadRecord, RunState, RunStatus, SearchHit
from ..persistence.manifest import ManifestStore
from ..runtime_paths import manifest_database_path
from ..widget_naming import control_widget_id, table_widget_id, window_widget_id
from ..window_layout import (
    set_preferred_split_screen_layout,
    split_screen_layout_for_widget,
)
from ..workers.search_worker import SearchRunWorker
from .locale_combo import build_locale_combo
from .setup_wizard import run_setup_wizard

if TYPE_CHECKING:
    type WorkerFactory = type[SearchRunWorker]

_WINDOW_GEOMETRY_KEY = "ui/main_window/geometry"


class MainWindow(QMainWindow):
    """Drive the bulk PDF search and download workflow."""

    def __init__(
        self,
        config: AppConfig,
        parent: QWidget | None = None,
        *,
        worker_factory: WorkerFactory = SearchRunWorker,
    ) -> None:
        super().__init__(parent)
        self.window_id = "main"
        self._config = config
        self._worker: SearchRunWorker | None = None
        self._worker_factory = worker_factory
        self._row_for_source_url: dict[str, int] = {}
        self._has_saved_geometry = False
        self._build_ui()
        self._load_config_into_controls(config)
        self._build_menu()
        self._restore_window_geometry()
        self.query_edit.setText(last_query())

    def _build_ui(self) -> None:
        """Build the main window widgets."""

        self.setWindowTitle(APP_DISPLAY_NAME)
        central = QWidget(self)
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        form = QFormLayout()
        self.query_edit = QLineEdit(self)
        self.google_checkbox = QCheckBox("Google", self)
        self.bing_checkbox = QCheckBox("Bing", self)
        provider_row = QHBoxLayout()
        provider_row.addWidget(self.google_checkbox)
        provider_row.addWidget(self.bing_checkbox)
        provider_row.addStretch(1)

        self.locale_combo = build_locale_combo(
            self,
            current_language=self._config.search.default_language,
            current_market=self._config.search.default_market,
        )
        self.max_pages_spin = QSpinBox(self)
        self.max_pages_spin.setRange(1, 10)
        self.max_results_spin = QSpinBox(self)
        self.max_results_spin.setRange(1, 200)
        self.download_timeout_spin = QSpinBox(self)
        self.download_timeout_spin.setRange(5, 120)
        self.download_timeout_spin.setSuffix(" sec")
        self.output_dir_edit = QLineEdit(self)
        self.output_dir_button = QPushButton("Browse...", self)
        self.output_dir_button.clicked.connect(self._browse_output_dir)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_dir_edit)
        output_row.addWidget(self.output_dir_button)

        form.addRow("Query", self.query_edit)
        form.addRow("Providers", provider_row)
        form.addRow("Locale", self.locale_combo)
        form.addRow("Max pages", self.max_pages_spin)
        form.addRow("Max results", self.max_results_spin)
        form.addRow("Download timeout", self.download_timeout_spin)
        form.addRow("Output directory", output_row)
        root.addLayout(form)

        button_row = QHBoxLayout()
        self.start_button = QPushButton("Start download run", self)
        self.stop_button = QPushButton("Stop", self)
        self.resume_button = QPushButton("Resume browser", self)
        self.settings_button = QPushButton("Settings", self)
        self.resume_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.start_button.clicked.connect(self.start_search)
        self.stop_button.clicked.connect(self.stop_search)
        self.resume_button.clicked.connect(self.resume_search)
        self.settings_button.clicked.connect(self.open_settings)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.resume_button)
        button_row.addWidget(self.settings_button)
        button_row.addStretch(1)
        root.addLayout(button_row)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready.", self)
        root.addWidget(self.status_label)

        self.results_table = QTableWidget(0, 5, self)
        self.results_table.setHorizontalHeaderLabels(
            ["Provider", "Title", "Status", "Source", "Output"]
        )
        results_header = self.results_table.horizontalHeader()
        results_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.results_table, stretch=1)
        self.fit_results_columns()

        assign_widget_identity(
            self,
            widget_id=window_widget_id(self.window_id),
            widget_alias="window.main",
        )
        assign_widget_identity(
            self.query_edit,
            widget_id=control_widget_id(self.window_id, "query"),
            widget_alias="main.query",
        )
        assign_widget_identity(
            self.start_button,
            widget_id=control_widget_id(self.window_id, "start"),
            widget_alias="main.start",
        )
        assign_widget_identity(
            self.resume_button,
            widget_id=control_widget_id(self.window_id, "resume"),
            widget_alias="main.resume",
        )
        assign_widget_identity(
            self.results_table,
            widget_id=table_widget_id(self.window_id, "results"),
            widget_alias="main.results",
        )

    def _build_menu(self) -> None:
        """Build a small file menu."""

        file_menu = self.menuBar().addMenu("&File")
        settings_action = QAction("Settings", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)

        open_ini_action = QAction("Open Settings INI", self)
        open_ini_action.triggered.connect(self._open_settings_ini)
        file_menu.addAction(open_ini_action)

        clear_manifest_action = QAction("Clear Manifest", self)
        clear_manifest_action.triggered.connect(self.clear_manifest)
        file_menu.addAction(clear_manifest_action)

        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Alt+X")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = self.menuBar().addMenu("&View")
        fit_columns_action = QAction("Fit Columns", self)
        fit_columns_action.triggered.connect(self.fit_results_columns)
        view_menu.addAction(fit_columns_action)

    def _load_config_into_controls(self, config: AppConfig) -> None:
        """Reflect one config object into the visible controls."""

        self.google_checkbox.setChecked(config.providers.google_enabled)
        self.bing_checkbox.setChecked(config.providers.bing_enabled)
        locale_option = locale_option_for_codes(
            config.search.default_language,
            config.search.default_market,
        )
        self.locale_combo.setCurrentText(locale_option.label)
        self.max_pages_spin.setValue(config.search.max_pages)
        self.max_results_spin.setValue(config.search.max_results)
        self.download_timeout_spin.setValue(config.downloads.timeout_seconds)
        self.output_dir_edit.setText(str(config.downloads.output_dir))

    def _config_from_controls(self) -> AppConfig:
        """Build one config object from the current widget values."""

        locale_option = locale_option_for_label(self.locale_combo.currentText())
        return replace(
            self._config,
            providers=replace(
                self._config.providers,
                google_enabled=self.google_checkbox.isChecked(),
                bing_enabled=self.bing_checkbox.isChecked(),
            ),
            search=replace(
                self._config.search,
                default_language=locale_option.language,
                default_market=locale_option.market,
                max_pages=int(self.max_pages_spin.value()),
                max_results=int(self.max_results_spin.value()),
            ),
            downloads=replace(
                self._config.downloads,
                output_dir=Path(self.output_dir_edit.text().strip()),
                timeout_seconds=int(self.download_timeout_spin.value()),
            ),
        )

    def _browse_output_dir(self) -> None:
        """Pick the output directory from a file dialog."""

        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose output directory",
            self.output_dir_edit.text(),
        )
        if chosen:
            self.output_dir_edit.setText(chosen)

    def _append_hit(self, hit: SearchHit) -> None:
        """Append one result row to the table."""

        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        self.results_table.setItem(row, 0, QTableWidgetItem(hit.provider_id.value))
        self.results_table.setItem(row, 1, QTableWidgetItem(hit.title))
        self.results_table.setItem(row, 2, QTableWidgetItem("Queued"))
        self.results_table.setItem(row, 3, QTableWidgetItem(hit.url))
        self.results_table.setItem(row, 4, QTableWidgetItem(""))
        self._apply_row_status_style(row, "Queued")
        self._row_for_source_url[hit.url] = row
        self._scroll_results_to_latest()

    def _update_row_with_download(self, record: DownloadRecord) -> None:
        """Update the table row that corresponds to one download result."""

        row = self._row_for_source_url.get(record.source_url)
        if row is None:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(
                row,
                0,
                QTableWidgetItem(record.provider_id.value),
            )
            self.results_table.setItem(row, 1, QTableWidgetItem(record.title))
            self.results_table.setItem(row, 3, QTableWidgetItem(record.source_url))
        status_text = self._status_text(record)
        self.results_table.setItem(row, 2, QTableWidgetItem(status_text))
        self.results_table.setItem(
            row,
            4,
            QTableWidgetItem(self._output_text(record)),
        )
        self._apply_row_status_style(row, status_text)
        self._scroll_results_to_latest()

    def _status_text(self, record: DownloadRecord) -> str:
        """Return the user-facing status label for one result row."""

        if record.outcome is DownloadOutcome.DOWNLOADED:
            return "Downloaded"
        if record.outcome is DownloadOutcome.SKIPPED:
            return "Skipped Duplicate"
        return "Failed"

    def _output_text(self, record: DownloadRecord) -> str:
        """Return the user-facing output/detail text for one result row."""

        if (
            record.outcome is DownloadOutcome.DOWNLOADED
            and record.output_path is not None
        ):
            return f"Saved to {record.output_path}"
        if record.outcome is DownloadOutcome.SKIPPED and record.output_path is not None:
            return f"{record.message} Existing file: {record.output_path}"
        return record.message

    def fit_results_columns(self) -> None:
        """Resize result columns to fit their current contents."""

        self.results_table.resizeColumnsToContents()
        self.results_table.horizontalHeader().setStretchLastSection(True)

    def _scroll_results_to_latest(self) -> None:
        """Keep the results table scrolled to the newest row."""

        self.results_table.scrollToBottom()

    def _apply_row_status_style(self, row: int, status_text: str) -> None:
        """Apply a light status-based background color across one row."""

        background = self._status_background(status_text)
        for column in range(self.results_table.columnCount()):
            item = self.results_table.item(row, column)
            if item is not None:
                item.setBackground(background)

    def _status_background(self, status_text: str) -> QBrush:
        """Return the light background brush for one status label."""

        normalized_status = status_text.strip().lower()
        if normalized_status == "downloaded":
            return QBrush(QColor("#e6f6ea"))
        if normalized_status == "skipped duplicate":
            return QBrush(QColor("#fff7db"))
        if normalized_status == "failed":
            return QBrush(QColor("#fdeaea"))
        return QBrush(QColor("#eef3ff"))

    def _set_running_state(self, running: bool) -> None:
        """Toggle the UI affordances for one worker run."""

        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.progress_bar.setVisible(running)

    def start_search(self) -> None:
        """Start a new search and download run."""

        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Busy", "A download run is already active.")
            return

        query = self.query_edit.text().strip()
        if not query:
            QMessageBox.warning(self, "Validation", "Enter a query before starting.")
            return

        config = self._config_from_controls()
        if not config.enabled_providers():
            QMessageBox.warning(
                self,
                "Validation",
                "Enable at least one provider before starting.",
            )
            return

        self._config = config
        save_config(config)
        set_last_query(query)
        self.results_table.setRowCount(0)
        self._row_for_source_url.clear()
        set_preferred_split_screen_layout(split_screen_layout_for_widget(self))

        request = config.build_request(query)
        self._worker = self._worker_factory(request, config)
        self._worker.progress_changed.connect(self.status_label.setText)
        self._worker.hit_discovered.connect(self._append_hit)
        self._worker.download_completed.connect(self._update_row_with_download)
        self._worker.manual_intervention_required.connect(self._on_manual_intervention)
        self._worker.run_finished.connect(self._on_run_finished)
        self._set_running_state(True)
        self.status_label.setText("Starting browser-driven PDF collection run...")
        self._worker.start()

    def stop_search(self) -> None:
        """Request cancellation for the active worker."""

        if self._worker is None:
            return
        self._worker.requestInterruption()
        self.status_label.setText("Stopping active run...")

    def resume_search(self) -> None:
        """Resume the browser flow after manual intervention."""

        if self._worker is None:
            return
        self._worker.resume_browser()
        self.resume_button.setEnabled(False)
        self.status_label.setText("Resuming browser workflow...")

    def open_settings(self) -> None:
        """Open the setup dialog and persist any accepted changes."""

        configured = run_setup_wizard(self._config, parent=self)
        if configured is None:
            return
        self._config = configured
        save_config(configured)
        self._load_config_into_controls(configured)
        self.status_label.setText("Updated application settings.")

    def _open_settings_ini(self) -> None:
        """Open the QSettings INI file in the default editor."""

        ini_path = Path(config_store_file_path())
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        ini_path.touch(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ini_path)))

    def clear_manifest(self) -> None:
        """Clear the persisted duplicate-manifest records after confirmation."""

        answer = QMessageBox.question(
            self,
            "Clear Manifest",
            "Clear all manifest entries? Existing downloaded files will be kept.",
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        ManifestStore(manifest_database_path()).clear()
        self.status_label.setText("Cleared manifest entries.")

    def _on_manual_intervention(
        self,
        provider_id: str,
        reason: str,
        page_url: str,
    ) -> None:
        """Handle the worker's manual-intervention signal."""

        self.resume_button.setEnabled(True)
        self.status_label.setText(
            self._manual_intervention_status_text(provider_id, reason)
        )
        self.statusBar().showMessage(page_url)

    def _manual_intervention_status_text(
        self,
        provider_id: str,
        reason: str,
    ) -> str:
        """Build the visible main-window status text for one intervention."""

        normalized_reason = reason.strip().lower()
        if normalized_reason == "cloudflare":
            return (
                "Cloudflare verification detected; solve it in the browser, then "
                "press Resume."
            )
        if normalized_reason == "interstitial":
            return (
                "Browser interstitial detected; complete it in the browser, then "
                "press Resume."
            )
        return (
            f"{provider_id.title()} requires manual {normalized_reason} handling "
            "in the browser, then press Resume."
        )

    def _on_run_finished(self, state: RunState) -> None:
        """Handle worker completion."""

        self._set_running_state(False)
        self.resume_button.setEnabled(False)
        if state.status is RunStatus.COMPLETED:
            summary = (
                f"Done. Downloaded {state.downloads_completed}, "
                f"skipped {state.duplicates_skipped}, failed {state.failures}."
            )
            self.status_label.setText(summary)
            return
        if state.status is RunStatus.CANCELLED:
            self.status_label.setText("Run cancelled.")
            return
        self.status_label.setText(state.last_message or "Run failed.")

    def _restore_window_geometry(self) -> None:
        """Restore the last saved main-window geometry when available."""

        raw_geometry = QSettings().value(_WINDOW_GEOMETRY_KEY)
        if isinstance(raw_geometry, QByteArray):
            self._has_saved_geometry = self.restoreGeometry(raw_geometry)

    def _save_window_geometry(self) -> None:
        """Persist the current main-window geometry."""

        settings = QSettings()
        settings.setValue(_WINDOW_GEOMETRY_KEY, self.saveGeometry())
        settings.sync()

    def has_saved_window_geometry(self) -> bool:
        """Return whether the window restored a previously saved geometry."""

        return self._has_saved_geometry

    def closeEvent(self, event: QCloseEvent) -> None:
        """Gracefully stop the worker when the window closes."""

        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(2_000)
        self._save_window_geometry()
        super().closeEvent(event)
