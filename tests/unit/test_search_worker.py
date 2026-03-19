from __future__ import annotations

import pytest

from pdf_search_downloader_ui.browser_session import (
    BrowserDownloadTriggeredError,
    BrowserNavigationError,
)
from pdf_search_downloader_ui.config import get_default_config
from pdf_search_downloader_ui.models import (
    BrowserPageSnapshot,
    ManualInterventionReason,
    PdfCandidate,
    ProviderId,
    RunState,
    SearchHit,
)
from pdf_search_downloader_ui.providers.google import GoogleProvider
from pdf_search_downloader_ui.workers.search_worker import SearchRunWorker


class _FakeProvider:
    """Provide the subset of the provider contract used by these tests."""

    provider_id = ProviderId.GOOGLE
    display_name = "Google"

    def resolve_pdf_candidate(
        self,
        hit: SearchHit,
        landing_html: str,
        landing_url: str,
    ) -> PdfCandidate | None:
        del landing_html, landing_url
        return None


class _FakeBrowserSession:
    """Provide a stand-in browser session for worker helper tests."""

    def fetch_snapshot(self, url: str) -> BrowserPageSnapshot:
        del url
        raise AssertionError("fetch_snapshot should be stubbed in the test")

    def current_snapshot(self) -> BrowserPageSnapshot:
        raise AssertionError("current_snapshot should be stubbed in the test")

    def wait_for_resume(
        self,
        should_cancel,
        *,
        poll_interval_seconds: float = 0.5,
    ) -> bool:
        del should_cancel, poll_interval_seconds
        raise AssertionError("wait_for_resume should not be used for blocked pages")


def test_resolve_candidate_uses_pdf_like_url_without_fetching(monkeypatch) -> None:
    worker = SearchRunWorker(
        get_default_config().build_request("equazioni"),
        get_default_config(),
    )
    hit = SearchHit(
        provider_id=ProviderId.GOOGLE,
        title="Equazioni di primo grado",
        url="https://example.com/report.pdf?download=1",
        display_url="example.com",
        snippet="",
        rank=1,
        page_number=0,
    )
    provider = GoogleProvider()
    fetch_called = False

    def fail_fetch(*_args, **_kwargs) -> str:
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("fetch should not be used for direct PDF-like URLs")

    monkeypatch.setattr(worker, "_fetch_ready_html", fail_fetch)

    candidate = worker._resolve_candidate_for_hit(
        browser_session=_FakeBrowserSession(),
        provider=provider,
        hit=hit,
        state=RunState(),
    )

    assert candidate is not None
    assert candidate.download_url == hit.url
    assert fetch_called is False


def test_resolve_candidate_treats_download_navigation_as_browser_candidate(
    monkeypatch,
) -> None:
    worker = SearchRunWorker(
        get_default_config().build_request("equazioni"),
        get_default_config(),
    )
    hit = SearchHit(
        provider_id=ProviderId.GOOGLE,
        title="Les matematica",
        url=(
            "https://www.cnuto.edu.it/index.php?option=com_phocadownload"
            "&view=category&download=3457:les-matematica&id=26:matematica"
        ),
        display_url="www.cnuto.edu.it",
        snippet="",
        rank=1,
        page_number=0,
    )
    provider = _FakeProvider()

    def raise_download_trigger(*_args, **_kwargs) -> str:
        raise BrowserDownloadTriggeredError(hit.url)

    monkeypatch.setattr(worker, "_fetch_ready_html", raise_download_trigger)

    candidate = worker._resolve_candidate_for_hit(
        browser_session=_FakeBrowserSession(),
        provider=provider,
        hit=hit,
        state=RunState(),
    )

    assert candidate is not None
    assert candidate.download_url == hit.url
    assert candidate.requires_browser_download is True


def test_resolve_candidate_skips_failed_landing_page_navigation(
    monkeypatch,
) -> None:
    worker = SearchRunWorker(
        get_default_config().build_request("equazioni"),
        get_default_config(),
    )
    hit = SearchHit(
        provider_id=ProviderId.GOOGLE,
        title="Resource view",
        url="https://elearning.unite.it/mod/resource/view.php?id=128643",
        display_url="elearning.unite.it",
        snippet="",
        rank=1,
        page_number=0,
    )
    provider = _FakeProvider()

    def raise_navigation_error(*_args, **_kwargs) -> str:
        raise BrowserNavigationError(hit.url, "net::ERR_CONNECTION_TIMED_OUT")

    monkeypatch.setattr(worker, "_fetch_ready_html", raise_navigation_error)

    candidate = worker._resolve_candidate_for_hit(
        browser_session=_FakeBrowserSession(),
        provider=provider,
        hit=hit,
        state=RunState(),
    )

    assert candidate is None


def test_fetch_ready_html_skips_blocked_page_after_short_wait(monkeypatch) -> None:
    worker = SearchRunWorker(
        get_default_config().build_request("naive bayes"),
        get_default_config(),
    )
    provider = _FakeProvider()
    blocked_snapshot = BrowserPageSnapshot(
        requested_url="https://example.com/blocked",
        final_url="https://example.com/blocked",
        title="Blocked",
        html="<html>access denied</html>",
        intervention_reason=ManualInterventionReason.BLOCKED,
    )
    browser_session = _FakeBrowserSession()

    monkeypatch.setattr(
        browser_session,
        "fetch_snapshot",
        lambda _url: blocked_snapshot,
    )
    monkeypatch.setattr(browser_session, "current_snapshot", lambda: blocked_snapshot)
    monkeypatch.setattr(worker, "_cancelled", lambda: False)
    monkeypatch.setattr(
        "pdf_search_downloader_ui.workers.search_worker.time.sleep",
        lambda _seconds: None,
    )
    time_points = iter((0.0, 1.0, 2.0, 6.0))
    monkeypatch.setattr(
        "pdf_search_downloader_ui.workers.search_worker.time.monotonic",
        lambda: next(time_points),
    )

    with pytest.raises(BrowserNavigationError, match="blocked"):
        worker._fetch_ready_html(
            browser_session,
            "https://example.com/blocked",
            provider,
            RunState(),
        )


def test_fetch_ready_html_recovers_when_blocked_page_clears(monkeypatch) -> None:
    worker = SearchRunWorker(
        get_default_config().build_request("naive bayes"),
        get_default_config(),
    )
    provider = _FakeProvider()
    blocked_snapshot = BrowserPageSnapshot(
        requested_url="https://example.com/blocked",
        final_url="https://example.com/blocked",
        title="Blocked",
        html="<html>access denied</html>",
        intervention_reason=ManualInterventionReason.BLOCKED,
    )
    recovered_snapshot = BrowserPageSnapshot(
        requested_url="https://example.com/blocked",
        final_url="https://example.com/recovered",
        title="Recovered",
        html="<html>ok</html>",
        intervention_reason=None,
    )
    browser_session = _FakeBrowserSession()
    snapshots = iter((blocked_snapshot, recovered_snapshot))

    monkeypatch.setattr(
        browser_session,
        "fetch_snapshot",
        lambda _url: blocked_snapshot,
    )
    monkeypatch.setattr(browser_session, "current_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(worker, "_cancelled", lambda: False)
    monkeypatch.setattr(
        "pdf_search_downloader_ui.workers.search_worker.time.sleep",
        lambda _seconds: None,
    )
    time_points = iter((0.0, 1.0, 1.5))
    monkeypatch.setattr(
        "pdf_search_downloader_ui.workers.search_worker.time.monotonic",
        lambda: next(time_points),
    )

    html = worker._fetch_ready_html(
        browser_session,
        "https://example.com/blocked",
        provider,
        RunState(),
    )

    assert html == "<html>ok</html>"
