from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from pdf_search_downloader_ui.config import get_default_config
from pdf_search_downloader_ui.locale_options import COMMON_LOCALE_LABELS
from pdf_search_downloader_ui.models import (
    DownloadOutcome,
    DownloadRecord,
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
    assert widget.results_table.item(0, 3).text() == "downloaded"
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

    config = window._config_from_controls()

    assert config.search.default_language == "en-US"
    assert config.search.default_market == "us"
