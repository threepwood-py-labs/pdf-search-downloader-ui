from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from pdf_search_downloader_ui.browser_session import (
    _FALLBACK_BROWSER_USER_AGENT,
    BrowserDownloadTriggeredError,
    BrowserNavigationError,
    BrowserSessionManager,
    _ensure_chromium_profile_settings,
    _stealth_init_script,
    detect_manual_intervention,
    ensure_ublock_origin_extension,
)
from pdf_search_downloader_ui.config import get_default_config
from pdf_search_downloader_ui.models import (
    BrowserPageSnapshot,
    ManualInterventionReason,
    ProviderId,
    SearchHit,
)
from pdf_search_downloader_ui.providers.bing import BingProvider
from pdf_search_downloader_ui.providers.google import GoogleProvider


def test_google_provider_build_url_forces_pdf_query() -> None:
    request = get_default_config().build_request("bilancio comunale")

    url = GoogleProvider().build_search_url(request, 2)

    assert url.startswith("https://www.google.it/search")
    assert "filetype%3Apdf" in url
    assert "hl=it" in url
    assert "gl=it" in url
    assert "start=20" in url


def test_bing_provider_build_url_uses_language_and_market() -> None:
    request = get_default_config().build_request("piano regolatore")

    url = BingProvider().build_search_url(request, 1)

    assert url.startswith("https://www.bing.com/search")
    assert "setlang=it" in url
    assert "cc=IT" in url
    assert "first=11" in url


def test_bing_provider_collect_hits_unwraps_redirect_links() -> None:
    encoded_target = base64.urlsafe_b64encode(b"https://example.com/report.pdf").decode(
        "ascii"
    )
    html = f"""
    <html><body>
      <a href="https://www.bing.com/ck/a?u=a1{encoded_target}">Comune report PDF</a>
      <a href="https://www.bing.com/preferences">Preferences</a>
    </body></html>
    """

    hits = BingProvider().collect_hits(
        html,
        "https://www.bing.com/search?q=test",
        page_number=0,
        max_results=10,
    )

    assert hits == [
        SearchHit(
            provider_id=ProviderId.BING,
            title="Comune report PDF",
            url="https://example.com/report.pdf",
            display_url="example.com",
            snippet="",
            rank=1,
            page_number=0,
        )
    ]


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


def test_google_provider_collect_hits_skips_youtube_links() -> None:
    html = """
    <html><body>
      <a href="https://www.youtube.com/watch?v=abc123">Naive Bayes lecture</a>
      <a href="https://example.com/report.pdf">Comune report PDF</a>
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
    assert (
        detect_manual_intervention(
            "<html>One last step before you continue</html>",
            "https://www.bing.com",
        ).value
        == "consent"
    )


def test_detect_manual_intervention_ignores_google_results_pages() -> None:
    assert (
        detect_manual_intervention(
            "<html><title>equazioni primo grado filetype:pdf - Cerca con Google</title>"
            "<script>window.__consent_state = 'accepted';</script></html>",
            "https://www.google.com/search?hl=it&gl=it&q=equazioni+primo+grado",
        )
        is None
    )


def test_browser_session_wait_for_resume_detects_resolved_page(
    tmp_path,
    monkeypatch,
) -> None:
    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )
    snapshots = iter(
        (
            BrowserPageSnapshot(
                requested_url="https://www.google.com",
                final_url="https://www.google.com",
                title="Wait",
                html="<html>captcha</html>",
                intervention_reason=ManualInterventionReason.CAPTCHA,
            ),
            BrowserPageSnapshot(
                requested_url="https://www.google.com/search?q=test",
                final_url="https://www.google.com/search?q=test",
                title="Results",
                html="<html>results</html>",
                intervention_reason=None,
            ),
        )
    )

    monkeypatch.setattr(session, "current_snapshot", lambda: next(snapshots))

    assert session.wait_for_resume(lambda: False, poll_interval_seconds=0.0) is True


def test_browser_session_current_snapshot_retries_transient_navigation_error(
    tmp_path,
    monkeypatch,
) -> None:
    class FakePage:
        """Provide the subset of the Playwright page interface used by the session."""

        def __init__(self) -> None:
            self.url = "https://www.google.com/search?q=test"
            self._content_calls = 0

        def wait_for_load_state(
            self,
            _load_state: str,
            *,
            timeout: int,
        ) -> None:
            del timeout

        def content(self) -> str:
            self._content_calls += 1
            if self._content_calls == 1:
                raise PlaywrightError(
                    "Page.content: Unable to retrieve content because the "
                    "page is navigating and changing the content."
                )
            return "<html>results</html>"

        def title(self) -> str:
            return "Results"

    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )
    fake_page = FakePage()
    monkeypatch.setattr(session, "_active_page", lambda: fake_page)
    monkeypatch.setattr(
        session,
        "_wait_for_page_settle",
        lambda _page: None,
    )
    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session.time.sleep",
        lambda _seconds: None,
    )

    snapshot = session.current_snapshot()

    assert snapshot.final_url == "https://www.google.com/search?q=test"
    assert snapshot.title == "Results"
    assert snapshot.html == "<html>results</html>"


def test_browser_session_fetch_snapshot_opens_a_fresh_tab(
    tmp_path,
    monkeypatch,
) -> None:
    class FakePage:
        """Provide the subset of the Playwright page interface used by the session."""

        def __init__(self) -> None:
            self.url = "about:blank"
            self.goto_calls: list[tuple[str, str]] = []
            self.brought_to_front = False

        def goto(self, url: str, *, wait_until: str) -> None:
            self.goto_calls.append((url, wait_until))
            self.url = url

        def bring_to_front(self) -> None:
            self.brought_to_front = True

        def is_closed(self) -> bool:
            return False

    class FakeContext:
        """Provide the Playwright context subset used by the session."""

        def __init__(self) -> None:
            self.created_pages: list[FakePage] = []
            self.pages: list[FakePage] = []

        def new_page(self) -> FakePage:
            page = FakePage()
            self.created_pages.append(page)
            self.pages.append(page)
            return page

    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )
    fake_context = FakeContext()
    original_page = fake_context.new_page()
    session._context = fake_context
    session._page = original_page

    def fake_capture(
        page: FakePage,
        *,
        requested_url: str,
    ) -> BrowserPageSnapshot:
        return BrowserPageSnapshot(
            requested_url=requested_url,
            final_url=page.url,
            title="Loaded",
            html="<html>loaded</html>",
            intervention_reason=None,
        )

    monkeypatch.setattr(session, "_capture_snapshot", fake_capture)

    snapshot = session.fetch_snapshot("https://example.com/report")

    assert snapshot.final_url == "https://example.com/report"
    assert len(fake_context.created_pages) == 2
    assert original_page.goto_calls == []
    assert fake_context.created_pages[1].goto_calls == [
        ("https://example.com/report", "domcontentloaded")
    ]
    assert fake_context.created_pages[1].brought_to_front is True
    assert session._page is fake_context.created_pages[1]


def test_browser_session_launches_persistent_context_with_stealth_settings(
    tmp_path,
    monkeypatch,
) -> None:
    class FakePage:
        """Provide the subset of the Playwright page interface used here."""

        def is_closed(self) -> bool:
            return False

    class FakeContext:
        """Capture context configuration applied during session startup."""

        def __init__(self) -> None:
            self.pages = [FakePage()]
            self.init_scripts: list[str] = []

        def add_init_script(self, script: str) -> None:
            self.init_scripts.append(script)

    class FakeChromium:
        """Capture Chromium launch options for the browser session."""

        def __init__(self) -> None:
            self.launch_kwargs: dict[str, object] = {}
            self.context = FakeContext()

        def launch_persistent_context(
            self,
            *,
            user_data_dir: str,
            **kwargs: object,
        ) -> FakeContext:
            self.launch_kwargs = {"user_data_dir": user_data_dir, **kwargs}
            return self.context

    class FakePlaywright:
        """Expose the Chromium launcher consumed by the session."""

        def __init__(self) -> None:
            self.chromium = FakeChromium()

        def stop(self) -> None:
            return None

    class FakeSyncPlaywright:
        """Stand in for ``playwright.sync_api.sync_playwright``."""

        def __init__(self) -> None:
            self.playwright = FakePlaywright()

        def start(self) -> FakePlaywright:
            return self.playwright

    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session.ensure_ublock_origin_extension",
        lambda _profile_dir: tmp_path / "extension",
    )
    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session._ensure_chromium_profile_settings",
        lambda _profile_dir, *, browser_download_dir: None,
    )
    fake_sync_playwright = FakeSyncPlaywright()
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        lambda: fake_sync_playwright,
    )
    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )

    session._ensure_ready()

    launch_kwargs = fake_sync_playwright.playwright.chromium.launch_kwargs
    assert launch_kwargs["user_data_dir"] == str(tmp_path / "profile")
    assert launch_kwargs["user_agent"] == _FALLBACK_BROWSER_USER_AGENT
    assert launch_kwargs["ignore_default_args"] == ["--enable-automation"]
    assert "--disable-blink-features=AutomationControlled" in launch_kwargs["args"]
    assert fake_sync_playwright.playwright.chromium.context.init_scripts == [
        _stealth_init_script("it")
    ]


def test_browser_session_fetch_snapshot_raises_when_navigation_starts_download(
    tmp_path,
    monkeypatch,
) -> None:
    class FakePage:
        """Provide the subset of the Playwright page interface used by the session."""

        def goto(self, url: str, *, wait_until: str) -> None:
            del url, wait_until
            raise PlaywrightError("Page.goto: Download is starting")

    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )
    monkeypatch.setattr(session, "_new_navigation_page", lambda: FakePage())

    with pytest.raises(BrowserDownloadTriggeredError):
        session.fetch_snapshot(
            "https://www.cnuto.edu.it/index.php?option=com_phocadownload"
        )


def test_browser_session_download_file_saves_inline_pdf_response(
    tmp_path,
    monkeypatch,
) -> None:
    class FakeResponse:
        """Provide the subset of the Playwright response interface used here."""

        def __init__(self) -> None:
            self.url = "https://upload.wikimedia.org/wikipedia/commons/c/c4/test.pdf"
            self.headers = {"content-type": "application/pdf"}

        def body(self) -> bytes:
            return b"%PDF-1.7 inline"

    class FakeDownloadContext:
        """Emulate a timed-out Playwright expect_download context manager."""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            raise PlaywrightTimeoutError(
                'Timeout 5000ms exceeded while waiting for event "download"'
            )

    class FakePage:
        """Provide the subset of the Playwright page interface used here."""

        def expect_download(self, *, timeout: int) -> FakeDownloadContext:
            assert timeout == 5_000
            return FakeDownloadContext()

        def goto(self, url: str, *, wait_until: str) -> FakeResponse:
            del url, wait_until
            return FakeResponse()

    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )
    monkeypatch.setattr(session, "_active_page", lambda: FakePage())

    download_path = session.download_file(
        "https://upload.wikimedia.org/wikipedia/commons/c/c4/test.pdf",
        tmp_path / "downloads",
    )

    assert download_path is not None
    assert download_path.name == "test.pdf"
    assert download_path.read_bytes() == b"%PDF-1.7 inline"


def test_browser_session_download_file_handles_download_starting_navigation(
    tmp_path,
    monkeypatch,
) -> None:
    class FakeDownload:
        """Provide the subset of the Playwright download interface used here."""

        suggested_filename = "test.pdf"

        def save_as(self, target_path: str) -> None:
            Path(target_path).write_bytes(b"%PDF-1.7 attachment")

    class FakeDownloadContext:
        """Emulate a successful Playwright expect_download context manager."""

        def __init__(self) -> None:
            self.value = FakeDownload()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            del exc_type, exc, tb
            return False

    class FakePage:
        """Provide the subset of the Playwright page interface used here."""

        def expect_download(self, *, timeout: int) -> FakeDownloadContext:
            assert timeout == 5_000
            return FakeDownloadContext()

        def goto(self, url: str, *, wait_until: str):
            del url, wait_until
            raise PlaywrightError("Page.goto: Download is starting")

    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )
    monkeypatch.setattr(session, "_active_page", lambda: FakePage())

    download_path = session.download_file(
        "https://upload.wikimedia.org/wikipedia/commons/c/c4/test.pdf",
        tmp_path / "downloads-target",
    )

    assert download_path is not None
    assert download_path.name == "test.pdf"
    assert download_path.read_bytes() == b"%PDF-1.7 attachment"


def test_browser_session_fetch_snapshot_wraps_navigation_failures(
    tmp_path,
    monkeypatch,
) -> None:
    class FakePage:
        """Provide the subset of the Playwright page interface used by the session."""

        def goto(self, url: str, *, wait_until: str) -> None:
            del url, wait_until
            raise PlaywrightError(
                "Page.goto: net::ERR_CONNECTION_TIMED_OUT at "
                "https://elearning.unite.it/mod/resource/view.php?id=128643"
            )

    session = BrowserSessionManager(
        tmp_path / "profile",
        locale="it",
        browser_download_dir=tmp_path / "downloads",
    )
    monkeypatch.setattr(session, "_new_navigation_page", lambda: FakePage())

    with pytest.raises(BrowserNavigationError):
        session.fetch_snapshot(
            "https://elearning.unite.it/mod/resource/view.php?id=128643"
        )


def test_ensure_ublock_origin_extension_installs_into_profile(
    tmp_path,
    monkeypatch,
) -> None:
    profile_dir = tmp_path / "profile"

    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session._latest_ublock_release_asset",
        lambda: (
            "2026.315.1814",
            "https://example.invalid/uBOLite_2026.315.1814.chromium.zip",
        ),
    )

    def fake_download(destination_path, asset_url: str) -> None:
        del asset_url
        with zipfile.ZipFile(destination_path, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "manifest_version": 3,
                        "name": "__MSG_extName__",
                        "version": "2026.315.1814",
                    }
                ),
            )
            archive.writestr("background.js", "console.log('ok');")

    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session._download_ublock_release_archive",
        fake_download,
    )

    extension_dir = ensure_ublock_origin_extension(profile_dir)

    assert extension_dir == profile_dir / "extensions" / "uBOLite.chromium"
    manifest = json.loads((extension_dir / "manifest.json").read_text("utf-8"))
    assert manifest["version"] == "2026.315.1814"
    assert (extension_dir / "background.js").is_file()


def test_ensure_ublock_origin_extension_reuses_existing_install(
    tmp_path,
    monkeypatch,
) -> None:
    profile_dir = tmp_path / "profile"
    extension_dir = profile_dir / "extensions" / "uBOLite.chromium"
    extension_dir.mkdir(parents=True)
    (extension_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "__MSG_extName__",
                "version": "2026.315.1814",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session._latest_ublock_release_asset",
        lambda: (_ for _ in ()).throw(AssertionError("network should not be used")),
    )

    installed_dir = ensure_ublock_origin_extension(profile_dir)

    assert installed_dir == extension_dir


def test_ensure_ublock_origin_extension_replaces_legacy_manifest_install(
    tmp_path,
    monkeypatch,
) -> None:
    profile_dir = tmp_path / "profile"
    extension_root = profile_dir / "extensions"
    legacy_extension_dir = extension_root / "uBlock0.chromium"
    legacy_extension_dir.mkdir(parents=True)
    (legacy_extension_dir / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 2,
                "name": "uBlock Origin",
                "version": "1.70.0",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session._latest_ublock_release_asset",
        lambda: (
            "2026.315.1814",
            "https://example.invalid/uBOLite_2026.315.1814.chromium.zip",
        ),
    )

    def fake_download(destination_path, asset_url: str) -> None:
        del asset_url
        with zipfile.ZipFile(destination_path, "w") as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "manifest_version": 3,
                        "name": "__MSG_extName__",
                        "version": "2026.315.1814",
                    }
                ),
            )

    monkeypatch.setattr(
        "pdf_search_downloader_ui.browser_session._download_ublock_release_archive",
        fake_download,
    )

    installed_dir = ensure_ublock_origin_extension(profile_dir)

    assert installed_dir == extension_root / "uBOLite.chromium"
    assert legacy_extension_dir.exists() is False


def test_ensure_chromium_profile_settings_forces_download_preferences(
    tmp_path,
) -> None:
    profile_dir = tmp_path / "profile"
    browser_download_dir = tmp_path / "downloads"

    _ensure_chromium_profile_settings(
        profile_dir,
        browser_download_dir=browser_download_dir,
    )

    preferences = json.loads(
        (profile_dir / "Default" / "Preferences").read_text(encoding="utf-8")
    )
    local_state = json.loads((profile_dir / "Local State").read_text(encoding="utf-8"))

    assert preferences["download"]["default_directory"] == str(browser_download_dir)
    assert preferences["download"]["prompt_for_download"] is False
    assert preferences["plugins"]["always_open_pdf_externally"] is True
    assert preferences["profile"]["exit_type"] == "Normal"
    assert local_state["user_experience_metrics"]["stability"]["exited_cleanly"] is True
    assert local_state["was"]["restarted"] is False
