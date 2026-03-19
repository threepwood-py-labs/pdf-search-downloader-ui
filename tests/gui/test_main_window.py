from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, QSettings, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox

from pdf_search_downloader_ui.config import get_default_config
from pdf_search_downloader_ui.locale_options import COMMON_LOCALE_LABELS
from pdf_search_downloader_ui.models import (
    DownloadOutcome,
    DownloadRecord,
    ProviderId,
    RunState,
    RunStatus,
    SearchHit,
)
from pdf_search_downloader_ui.ui import MainWindow


class FakeWorker(QObject):
    """Simple synchronous worker used by the GUI tests."""

    progress_changed = Signal(str)
    hit_discovered = Signal(object)
    download_completed = Signal(object)
    manual_intervention_required = Signal(str, str, str)
    run_finished = Signal(object)

    def __init__(self, request, config) -> None:
        super().__init__()
        self._request = request
        self._config = config
        self._running = False
        self.resume_calls = 0

    def isRunning(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        hit = SearchHit(
            provider_id=self._request.providers[0],
            title="Comune report",
            url="https://example.com/report.pdf",
            display_url="example.com",
            snippet="",
            rank=1,
            page_number=0,
        )
        record = DownloadRecord(
            provider_id=hit.provider_id,
            title=hit.title,
            source_url=hit.url,
            final_url=hit.url,
            output_path=self._config.downloads.output_dir / "Comune report.pdf",
            outcome=DownloadOutcome.DOWNLOADED,
            sha256_hex="abc123",
            file_size_bytes=1024,
            message="Downloaded PDF successfully.",
        )
        self.progress_changed.emit("Running fake worker")
        self.hit_discovered.emit(hit)
        self.download_completed.emit(record)
        self._running = False
        self.run_finished.emit(
            RunState(
                status=RunStatus.COMPLETED,
                hits_seen=1,
                downloads_completed=1,
                duplicates_skipped=0,
                failures=0,
                last_message="done",
            )
        )

    def requestInterruption(self) -> None:
        self._running = False

    def resume_browser(self) -> None:
        self.resume_calls += 1

    def wait(self, _timeout: int) -> None:
        self._running = False


def test_main_window_widget_identity_contract(window) -> None:
    assert str(window.property("widget_id")) == "window:main"
    assert str(window.query_edit.property("widget_id")) == "window:main:control:query"
    assert (
        str(window.results_table.property("widget_id")) == "window:main:table:results"
    )


def test_main_window_start_search_runs_fake_worker(qtbot, tmp_path) -> None:
    config = get_default_config()
    config = config.__class__(
        providers=config.providers,
        search=config.search,
        downloads=config.downloads.__class__(
            output_dir=tmp_path / "downloads",
            skip_duplicates=config.downloads.skip_duplicates,
            timeout_seconds=config.downloads.timeout_seconds,
        ),
        browser=config.browser,
        setup_completed=True,
    )
    widget = MainWindow(config, worker_factory=FakeWorker)
    qtbot.addWidget(widget)
    widget.show()
    widget.query_edit.setText("bilancio comune")

    widget.start_search()

    assert widget.results_table.rowCount() == 1
    assert widget.results_table.item(0, 2).text() == "Downloaded"
    assert "Downloaded 1" in widget.status_label.text()
    assert widget.start_button.isEnabled() is True
    assert widget.stop_button.isEnabled() is False


def test_main_window_manual_intervention_enables_resume(window) -> None:
    window._on_manual_intervention("google", "captcha", "https://www.google.com")

    assert window.resume_button.isEnabled() is True
    assert "manual captcha" in window.status_label.text().lower()


def test_main_window_locale_combo_uses_common_defaults(window) -> None:
    combo_items = [
        window.locale_combo.itemText(index)
        for index in range(window.locale_combo.count())
    ]

    assert window.locale_combo.currentText() == "Italian"
    assert combo_items == list(COMMON_LOCALE_LABELS)


def test_main_window_locale_combo_drives_language_and_market(window) -> None:
    window.locale_combo.setCurrentText("English (US)")
    window.download_timeout_spin.setValue(18)

    config = window._config_from_controls()

    assert config.search.default_language == "en-US"
    assert config.search.default_market == "us"
    assert config.downloads.timeout_seconds == 18


def test_main_window_formats_duplicate_rows_clearly(window) -> None:
    record = DownloadRecord(
        provider_id=ProviderId.GOOGLE,
        title="Comune report",
        source_url="https://example.com/result",
        final_url="https://example.com/report.pdf",
        output_path=window._config.downloads.output_dir / "report.pdf",
        outcome=DownloadOutcome.SKIPPED,
        sha256_hex="abc123",
        file_size_bytes=1024,
        message="Skipped duplicate already stored in manifest.",
    )

    window._update_row_with_download(record)

    assert window.results_table.item(0, 2).text() == "Skipped Duplicate"
    assert "Existing file:" in window.results_table.item(0, 4).text()


def test_main_window_queued_rows_use_reordered_columns(window) -> None:
    hit = SearchHit(
        provider_id=ProviderId.GOOGLE,
        title="Comune report",
        url="https://example.com/report.pdf",
        display_url="example.com",
        snippet="",
        rank=1,
        page_number=0,
    )

    window._append_hit(hit)

    assert window.results_table.item(0, 0).text() == "google"
    assert window.results_table.item(0, 1).text() == "Comune report"
    assert window.results_table.item(0, 2).text() == "Queued"
    assert window.results_table.item(0, 3).text() == "https://example.com/report.pdf"
    assert window.results_table.item(0, 0).background().color().name() == "#eef3ff"


def test_main_window_results_autoscroll_to_bottom(qtbot, window) -> None:
    window.resize(700, 260)
    window.show()
    qtbot.waitExposed(window)

    for index in range(30):
        window._append_hit(
            SearchHit(
                provider_id=ProviderId.GOOGLE,
                title=f"Comune report {index}",
                url=f"https://example.com/report-{index}.pdf",
                display_url="example.com",
                snippet="",
                rank=index + 1,
                page_number=0,
            )
        )

    scrollbar = window.results_table.verticalScrollBar()
    assert scrollbar.value() == scrollbar.maximum()


def test_main_window_exit_shortcut_and_fit_columns_action(window) -> None:
    actions_by_text = {action.text(): action for action in window.findChildren(QAction)}
    exit_action = actions_by_text["Exit"]
    fit_columns_action = actions_by_text["Fit Columns"]

    assert exit_action.shortcut().toString() == "Alt+X"
    fit_columns_action.trigger()

    header = window.results_table.horizontalHeader()
    assert header.sectionResizeMode(0) == header.ResizeMode.Interactive


def test_main_window_clear_manifest_action(window, monkeypatch) -> None:
    captured: list[str] = []

    monkeypatch.setattr(
        "pdf_search_downloader_ui.ui.main_window.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "pdf_search_downloader_ui.ui.main_window.ManifestStore.clear",
        lambda self: captured.append(str(self.database_path)),
    )

    window.clear_manifest()

    assert captured
    assert window.status_label.text() == "Cleared manifest entries."


def test_main_window_saves_geometry_on_close(qtbot) -> None:
    window = MainWindow(get_default_config())
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert isinstance(QSettings().value("ui/main_window/geometry"), QByteArray)
