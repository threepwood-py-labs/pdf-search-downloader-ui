from __future__ import annotations

from pdf_search_downloader_ui.browser_session import (
    BrowserDownloadTriggeredError,
    BrowserNavigationError,
)
from pdf_search_downloader_ui.config import get_default_config
from pdf_search_downloader_ui.models import (
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
