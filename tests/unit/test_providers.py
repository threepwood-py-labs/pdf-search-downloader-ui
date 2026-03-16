from __future__ import annotations

from pdf_search_downloader_ui.browser_session import detect_manual_intervention
from pdf_search_downloader_ui.config import get_default_config
from pdf_search_downloader_ui.models import ProviderId, SearchHit
from pdf_search_downloader_ui.providers.bing import BingProvider
from pdf_search_downloader_ui.providers.google import GoogleProvider


def test_google_provider_build_url_forces_pdf_query() -> None:
    request = get_default_config().build_request("bilancio comunale")

    url = GoogleProvider().build_search_url(request, 2)

    assert "filetype%3Apdf" in url
    assert "hl=it" in url
    assert "start=20" in url


def test_bing_provider_build_url_uses_language_and_market() -> None:
    request = get_default_config().build_request("piano regolatore")

    url = BingProvider().build_search_url(request, 1)

    assert "setlang=it" in url
    assert "cc=IT" in url
    assert "first=11" in url


def test_google_provider_collect_hits_unwraps_redirect_links() -> None:
    html = """
    <html><body>
      <a href="/url?q=https://example.com/report.pdf&sa=U">Comune report PDF</a>
      <a href="https://www.google.com/preferences">Preferences</a>
    </body></html>
    """

    hits = GoogleProvider().collect_hits(
        html,
        "https://www.google.com/search?q=test",
        page_number=0,
        max_results=10,
    )

    assert hits == [
        SearchHit(
            provider_id=ProviderId.GOOGLE,
            title="Comune report PDF",
            url="https://example.com/report.pdf",
            display_url="example.com",
            snippet="",
            rank=1,
            page_number=0,
        )
    ]


def test_provider_resolve_pdf_candidate_finds_one_hop_pdf() -> None:
    hit = SearchHit(
        provider_id=ProviderId.GOOGLE,
        title="Comune report",
        url="https://example.com/report",
        display_url="example.com",
        snippet="",
        rank=1,
        page_number=0,
    )
    landing_html = '<a href="/files/report.pdf">Download PDF</a>'

    candidate = GoogleProvider().resolve_pdf_candidate(
        hit,
        landing_html,
        "https://example.com/report",
    )

    assert candidate is not None
    assert candidate.download_url == "https://example.com/files/report.pdf"


def test_detect_manual_intervention_flags_common_blockers() -> None:
    assert (
        detect_manual_intervention(
            "<html>Before you continue to Google Search</html>",
            "https://www.google.com",
        ).value
        == "consent"
    )
    assert (
        detect_manual_intervention(
            "<html>Our systems have detected unusual traffic</html>",
            "https://www.google.com",
        ).value
        == "captcha"
    )
