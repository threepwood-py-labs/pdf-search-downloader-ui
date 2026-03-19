"""Playwright-backed headed browser session management."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import unquote, urlparse

import httpx

from .html_tools import looks_like_pdf_url
from .models import BrowserPageSnapshot, ManualInterventionReason
from .window_layout import preferred_split_screen_layout

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import (
        BrowserContext,
        Download,
        Page,
        Playwright,
        Response,
    )


logger = logging.getLogger(__name__)

_U_BLOCK_RELEASE_API_URL = (
    "https://api.github.com/repos/uBlockOrigin/uBOL-home/releases/latest"
)
_U_BLOCK_EXTENSION_DIR_NAME = "uBOLite.chromium"
_LEGACY_U_BLOCK_EXTENSION_DIR_NAME = "uBlock0.chromium"
_U_BLOCK_EXTENSION_TIMEOUT_SECONDS = 60.0
_U_BLOCK_EXTENSION_USER_AGENT = "pdf-search-downloader-ui/0.1.0"
_DOWNLOAD_EVENT_TIMEOUT_MS = 5_000
_CHROMIUM_PROFILE_DIR_NAME = "Default"
_CHROMIUM_PREFERENCES_FILE_NAME = "Preferences"
_CHROMIUM_LOCAL_STATE_FILE_NAME = "Local State"
_FALLBACK_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


class BrowserHttpRequestState:
    """Describe browser-like request headers and cookies for one URL."""

    __slots__ = ("cookies", "headers")

    def __init__(self, headers: dict[str, str], cookies: dict[str, str]) -> None:
        self.headers = headers
        self.cookies = cookies


class BrowserSessionProtocol(Protocol):
    """Describe the browser operations used by the worker and downloader."""

    def fetch_snapshot(self, url: str) -> BrowserPageSnapshot:
        """Open one URL and return the captured page snapshot."""

        ...

    def current_snapshot(self) -> BrowserPageSnapshot:
        """Return the snapshot for the current active page."""

        ...

    def wait_for_resume(
        self,
        should_cancel: Callable[[], bool],
        *,
        poll_interval_seconds: float = 0.1,
    ) -> bool:
        """Wait until the UI resumes the browser workflow or the page recovers."""

        ...

    def resume(self) -> None:
        """Resume a paused browser workflow."""

        ...

    def bring_to_front(self) -> None:
        """Bring the active browser page to the foreground."""

        ...

    def download_file(self, url: str, destination_dir: Path) -> Path | None:
        """Attempt to capture a browser-managed download."""

        ...

    def http_request_state(
        self,
        url: str,
        *,
        referer: str | None = None,
    ) -> BrowserHttpRequestState:
        """Build browser-like headers and cookies for one HTTP request."""

        ...


class BrowserDownloadTriggeredError(RuntimeError):
    """Signal that one browser navigation triggered a file download."""

    def __init__(self, requested_url: str) -> None:
        super().__init__(f"Browser navigation started a download for {requested_url}")
        self.requested_url = requested_url


class BrowserNavigationError(RuntimeError):
    """Signal that one browser navigation failed before content was captured."""

    def __init__(self, requested_url: str, details: str) -> None:
        super().__init__(f"Browser navigation failed for {requested_url}: {details}")
        self.requested_url = requested_url
        self.details = details


def detect_manual_intervention(
    html: str,
    final_url: str,
) -> ManualInterventionReason | None:
    """Detect whether one page requires manual user interaction."""

    parsed_url = urlparse(final_url)
    netloc = parsed_url.netloc.lower()
    path = parsed_url.path.lower()
    text = f"{final_url}\n{html}".lower()
    cloudflare_markers = (
        "__cf_chl",
        "cf-chl-",
        "challenges.cloudflare.com",
        "checking your browser before accessing",
        "enable javascript and cookies to continue",
    )
    if "cloudflare" in text and (
        "just a moment" in text or any(marker in text for marker in cloudflare_markers)
    ):
        return ManualInterventionReason.CLOUDFLARE
    captcha_markers = (
        "unusual traffic",
        "verify you're human",
        "verify you are human",
        "human verification",
        "not a robot",
        "i'm not a robot",
        "i am not a robot",
        "enter the characters you see below",
        "solve the captcha",
        "complete the captcha",
        "hcaptcha",
        "cf-turnstile",
    )
    if "/sorry/" in path or any(marker in text for marker in captcha_markers):
        return ManualInterventionReason.CAPTCHA
    if (
        netloc.startswith("consent.")
        or "/consent" in path
        or "before you continue" in text
        or "privacy & cookies" in text
        or "your privacy choices" in text
        or "one last step" in text
    ):
        return ManualInterventionReason.CONSENT
    if (
        "checking if the site connection is secure" in text
        or "please wait while your request is being verified" in text
        or "review the security of your connection before proceeding" in text
    ):
        return ManualInterventionReason.INTERSTITIAL
    if "access denied" in text or "blocked" in text:
        return ManualInterventionReason.BLOCKED
    return None


def _ublock_extension_dir(profile_dir: Path) -> Path:
    """Return the unpacked uBlock Origin Lite directory under one profile."""

    return profile_dir / "extensions" / _U_BLOCK_EXTENSION_DIR_NAME


def _chromium_preferences_path(profile_dir: Path) -> Path:
    """Return the Chromium profile preferences path."""

    return profile_dir / _CHROMIUM_PROFILE_DIR_NAME / _CHROMIUM_PREFERENCES_FILE_NAME


def _chromium_local_state_path(profile_dir: Path) -> Path:
    """Return the Chromium profile local-state path."""

    return profile_dir / _CHROMIUM_LOCAL_STATE_FILE_NAME


def _load_json_object(path: Path) -> dict[str, object]:
    """Load one JSON object file, returning an empty object when missing."""

    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return cast("dict[str, object]", payload)


def _write_json_object(path: Path, payload: dict[str, object]) -> None:
    """Write one JSON object file using Chromium-compatible formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        encoding="utf-8",
    )


def _ensure_json_object(container: dict[str, object], key: str) -> dict[str, object]:
    """Return one nested JSON object, replacing incompatible values."""

    current_value = container.get(key)
    if isinstance(current_value, dict):
        return cast("dict[str, object]", current_value)
    nested_object: dict[str, object] = {}
    container[key] = nested_object
    return nested_object


def _ensure_chromium_profile_settings(
    profile_dir: Path,
    *,
    browser_download_dir: Path,
) -> None:
    """Apply persistent Chromium settings for downloads and startup UX."""

    preferences_path = _chromium_preferences_path(profile_dir)
    local_state_path = _chromium_local_state_path(profile_dir)
    preferences = _load_json_object(preferences_path)
    local_state = _load_json_object(local_state_path)
    preferences["exit_type"] = "Normal"

    download_preferences = _ensure_json_object(preferences, "download")
    download_preferences["default_directory"] = str(browser_download_dir)
    download_preferences["directory_upgrade"] = True
    download_preferences["prompt_for_download"] = False

    savefile_preferences = _ensure_json_object(preferences, "savefile")
    savefile_preferences["default_directory"] = str(browser_download_dir)

    plugin_preferences = _ensure_json_object(preferences, "plugins")
    plugin_preferences["always_open_pdf_externally"] = True

    profile_preferences = _ensure_json_object(preferences, "profile")
    profile_preferences["exit_type"] = "Normal"

    user_metrics = _ensure_json_object(local_state, "user_experience_metrics")
    stability = _ensure_json_object(user_metrics, "stability")
    stability["exited_cleanly"] = True

    was_preferences = _ensure_json_object(local_state, "was")
    was_preferences["restarted"] = False

    _write_json_object(preferences_path, preferences)
    _write_json_object(local_state_path, local_state)


def _inline_pdf_filename(response: Response | None, fallback_url: str) -> str:
    """Build one filename for an inline PDF browser response."""

    if response is not None:
        response_path_name = Path(urlparse(str(response.url)).path).name
        if response_path_name:
            return unquote(response_path_name)
    fallback_path_name = Path(urlparse(fallback_url).path).name
    if fallback_path_name:
        return unquote(fallback_path_name)
    return "download.pdf"


def _accept_language_header(locale: str) -> str:
    """Build one browser-like Accept-Language header from the app locale."""

    normalized_locale = locale.replace("_", "-").strip()
    if not normalized_locale:
        return "en-US,en;q=0.9"
    language = normalized_locale.split("-", maxsplit=1)[0]
    if language == normalized_locale:
        return f"{language},{language};q=0.9,en-US;q=0.8,en;q=0.7"
    return f"{normalized_locale},{language};q=0.9,en-US;q=0.8,en;q=0.7"


def _navigator_languages(locale: str) -> tuple[str, ...]:
    """Build one browser-like ``navigator.languages`` sequence."""

    normalized_locale = locale.replace("_", "-").strip()
    if not normalized_locale:
        return ("en-US", "en")
    language = normalized_locale.split("-", maxsplit=1)[0]
    values: list[str] = [normalized_locale]
    if language != normalized_locale:
        values.append(language)
    values.extend(("en-US", "en"))
    return tuple(dict.fromkeys(values))


def _stealth_init_script(locale: str) -> str:
    """Build the stealth init script for one configured locale."""

    navigator_languages = json.dumps(
        _navigator_languages(locale),
        ensure_ascii=True,
    )
    return f"""
Object.defineProperty(navigator, 'webdriver', {{
  get: () => undefined,
}});
Object.defineProperty(navigator, 'languages', {{
  get: () => {navigator_languages},
}});
Object.defineProperty(navigator, 'plugins', {{
  get: () => [1, 2, 3, 4, 5],
}});
window.chrome = window.chrome || {{ runtime: {{}} }};
"""


def _sec_fetch_site_value(url: str, referer: str | None) -> str:
    """Build one browser-like Sec-Fetch-Site value."""

    if not referer:
        return "none"
    target_parts = urlparse(url)
    referer_parts = urlparse(referer)
    if (
        target_parts.scheme == referer_parts.scheme
        and target_parts.netloc.lower() == referer_parts.netloc.lower()
    ):
        return "same-origin"
    return "cross-site"


def _installed_ublock_version(extension_dir: Path) -> str | None:
    """Return the installed uBlock Origin Lite version when present."""

    manifest_path = extension_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    manifest_version = manifest.get("manifest_version")
    if manifest_version != 3:
        return None
    version = manifest.get("version")
    if not isinstance(version, str):
        return None
    normalized_version = version.strip()
    return normalized_version or None


def _latest_ublock_release_asset() -> tuple[str, str]:
    """Return the latest uBlock Origin Lite version and Chromium zip URL."""

    with httpx.Client(
        follow_redirects=True,
        timeout=_U_BLOCK_EXTENSION_TIMEOUT_SECONDS,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _U_BLOCK_EXTENSION_USER_AGENT,
        },
    ) as client:
        response = client.get(_U_BLOCK_RELEASE_API_URL)
        response.raise_for_status()
        payload_obj: object = response.json()
    if not isinstance(payload_obj, dict):
        raise RuntimeError(
            "GitHub returned an invalid uBlock Origin Lite release payload."
        )
    payload = cast("dict[str, object]", payload_obj)

    tag_name_obj = payload.get("tag_name")
    if not isinstance(tag_name_obj, str):
        raise RuntimeError(
            "GitHub release payload did not include a valid Lite tag name."
        )
    normalized_tag_name = tag_name_obj.strip().removeprefix("v")
    if not normalized_tag_name:
        raise RuntimeError(
            "GitHub release payload did not include a valid Lite tag name."
        )

    assets_obj = payload.get("assets")
    if not isinstance(assets_obj, list):
        raise RuntimeError(
            "GitHub release payload did not include Lite release assets."
        )
    for asset_obj in cast("list[object]", assets_obj):
        if not isinstance(asset_obj, dict):
            continue
        asset = cast("dict[str, object]", asset_obj)
        asset_name_obj = asset.get("name")
        asset_url_obj = asset.get("browser_download_url")
        if (
            isinstance(asset_name_obj, str)
            and asset_name_obj.endswith(".chromium.zip")
            and isinstance(asset_url_obj, str)
            and asset_url_obj.strip()
        ):
            return normalized_tag_name, asset_url_obj.strip()
    raise RuntimeError("Could not find the Chromium uBlock Origin Lite release asset.")


def _download_ublock_release_archive(destination_path: Path, asset_url: str) -> None:
    """Download one uBlock Origin Lite release archive to disk."""

    with httpx.stream(
        "GET",
        asset_url,
        follow_redirects=True,
        timeout=_U_BLOCK_EXTENSION_TIMEOUT_SECONDS,
        headers={"User-Agent": _U_BLOCK_EXTENSION_USER_AGENT},
    ) as response:
        response.raise_for_status()
        with destination_path.open("wb") as handle:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                handle.write(chunk)


def ensure_ublock_origin_extension(profile_dir: Path) -> Path:
    """Install uBlock Origin Lite into one Chromium profile when needed."""

    extension_dir = _ublock_extension_dir(profile_dir)
    installed_version = _installed_ublock_version(extension_dir)
    if installed_version is not None:
        logger.info(
            "Using installed uBlock Origin Lite extension version=%s path=%s",
            installed_version,
            extension_dir,
        )
        return extension_dir

    extension_root = extension_dir.parent
    legacy_extension_dir = extension_root / _LEGACY_U_BLOCK_EXTENSION_DIR_NAME
    extension_root.mkdir(parents=True, exist_ok=True)
    release_version, asset_url = _latest_ublock_release_asset()
    logger.info(
        "Installing uBlock Origin Lite version=%s into %s",
        release_version,
        extension_dir,
    )

    with tempfile.TemporaryDirectory(
        prefix="ublock-origin-",
        dir=extension_root,
    ) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        archive_path = temp_dir / "uBOLite.chromium.zip"
        staging_root = temp_dir / "staging"
        backup_dir = extension_root / f"{_U_BLOCK_EXTENSION_DIR_NAME}.bak"
        _download_ublock_release_archive(archive_path, asset_url)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(staging_root)

        staged_extension_dir = staging_root
        if not staged_extension_dir.is_dir():
            raise RuntimeError(
                "uBlock Origin Lite archive did not contain a valid root folder."
            )
        if _installed_ublock_version(staged_extension_dir) is None:
            raise RuntimeError(
                "uBlock Origin Lite archive did not contain a valid MV3 manifest."
            )

        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if extension_dir.exists():
            extension_dir.replace(backup_dir)
        if legacy_extension_dir.exists():
            shutil.rmtree(legacy_extension_dir)
        staged_extension_dir.replace(extension_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

    return extension_dir


class BrowserSessionManager(BrowserSessionProtocol):
    """Manage one visible persistent Playwright browser context."""

    _snapshot_retry_attempts = 5
    _snapshot_retry_delay_seconds = 0.2

    def __init__(
        self,
        profile_dir: Path,
        *,
        locale: str,
        browser_download_dir: Path,
    ) -> None:
        self._profile_dir = profile_dir
        self._locale = locale
        self._browser_download_dir = browser_download_dir
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._browser_user_agent: str | None = None
        self._last_snapshot = BrowserPageSnapshot(
            requested_url="",
            final_url="",
            title="",
            html="",
            intervention_reason=None,
        )

    def _ensure_ready(self) -> None:
        """Start the Playwright context when needed."""

        if self._context is not None and self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - dependency import seam
            raise RuntimeError(
                "Playwright is not installed in the current env."
            ) from exc

        self._profile_dir.mkdir(parents=True, exist_ok=True)
        self._browser_download_dir.mkdir(parents=True, exist_ok=True)
        extension_dir = ensure_ublock_origin_extension(self._profile_dir)
        browser_window_bounds = preferred_split_screen_layout().left
        _ensure_chromium_profile_settings(
            self._profile_dir,
            browser_download_dir=self._browser_download_dir,
        )
        logger.info("Launching Playwright browser context at %s", self._profile_dir)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            channel="chromium",
            headless=False,
            accept_downloads=True,
            locale=self._locale,
            user_agent=_FALLBACK_BROWSER_USER_AGENT,
            ignore_default_args=["--enable-automation"],
            args=[
                f"--disable-extensions-except={extension_dir}",
                f"--load-extension={extension_dir}",
                "--new-window",
                "--disable-blink-features=AutomationControlled",
                "--hide-crash-restore-bubble",
                "--disable-session-crashed-bubble",
                "--disable-features=TranslateUI",
                "--window-position="
                f"{browser_window_bounds.left},{browser_window_bounds.top}",
                "--window-size="
                f"{browser_window_bounds.width},{browser_window_bounds.height}",
            ],
        )
        self._context.add_init_script(_stealth_init_script(self._locale))
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

    def _browser_like_user_agent(self) -> str:
        """Return the browser user agent used by the persistent context."""

        if self._browser_user_agent is not None:
            return self._browser_user_agent
        page = self._active_page()
        raw_user_agent: object = page.evaluate("() => navigator.userAgent")
        if isinstance(raw_user_agent, str) and raw_user_agent.strip():
            self._browser_user_agent = raw_user_agent.strip()
        else:
            self._browser_user_agent = _FALLBACK_BROWSER_USER_AGENT
        return self._browser_user_agent

    def _cookies_for_url(self, url: str) -> dict[str, str]:
        """Return browser cookies that apply to one target URL."""

        self._ensure_ready()
        if self._context is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("Browser context is unavailable.")
        cookies = self._context.cookies(urls=[url])
        normalized_cookies: dict[str, str] = {}
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            if isinstance(name, str) and isinstance(value, str):
                normalized_cookies[name] = value
        return normalized_cookies

    def _active_page(self) -> Page:
        """Return the current active page after ensuring the context exists."""

        self._ensure_ready()
        if self._context is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("Browser context is unavailable.")
        open_pages = [page for page in self._context.pages if not page.is_closed()]
        if open_pages:
            self._page = open_pages[-1]
        elif self._page is None or self._page.is_closed():
            self._page = self._context.new_page()
        return self._page

    def _new_navigation_page(self) -> Page:
        """Create and return one fresh tab for crawl navigation."""

        self._ensure_ready()
        if self._context is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("Browser context is unavailable.")
        self._page = self._context.new_page()
        self._page.bring_to_front()
        return self._page

    def bring_to_front(self) -> None:
        """Bring the current browser page to the foreground."""

        page = self._active_page()
        page.bring_to_front()
        logger.info("Brought browser page to the foreground url=%s", page.url)

    def _wait_for_page_settle(self, page: Page) -> None:
        """Give one page a short chance to finish its current navigation."""

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        for load_state in ("domcontentloaded", "networkidle"):
            try:
                page.wait_for_load_state(load_state, timeout=1_500)
            except PlaywrightTimeoutError:
                continue

    def _is_transient_snapshot_error(self, exc: Exception) -> bool:
        """Return whether one Playwright snapshot error is transient."""

        message = str(exc).lower()
        return (
            "page is navigating and changing the content" in message
            or "page is navigating" in message
            or "execution context was destroyed" in message
        )

    def _is_download_triggered_navigation_error(self, exc: Exception) -> bool:
        """Return whether one navigation error means a download started."""

        return "download is starting" in str(exc).lower()

    def _capture_snapshot(
        self,
        page: Page,
        *,
        requested_url: str,
    ) -> BrowserPageSnapshot:
        """Capture one page snapshot, retrying through transient navigation."""

        from playwright.sync_api import Error as PlaywrightError

        last_error: PlaywrightError | None = None
        for attempt in range(1, self._snapshot_retry_attempts + 1):
            self._wait_for_page_settle(page)
            try:
                html = page.content()
                final_url = str(page.url)
                title = str(page.title())
            except PlaywrightError as exc:
                if not self._is_transient_snapshot_error(exc):
                    raise
                last_error = exc
                logger.info(
                    "Snapshot capture hit transient navigation attempt=%s/%s",
                    attempt,
                    self._snapshot_retry_attempts,
                )
                time.sleep(self._snapshot_retry_delay_seconds)
                continue
            snapshot = BrowserPageSnapshot(
                requested_url=requested_url,
                final_url=final_url,
                title=title,
                html=html,
                intervention_reason=detect_manual_intervention(html, final_url),
            )
            self._last_snapshot = snapshot
            return snapshot
        raise RuntimeError(
            "Browser page kept navigating while trying to capture a snapshot."
        ) from last_error

    def fetch_snapshot(self, url: str) -> BrowserPageSnapshot:
        """Open one URL in a fresh tab and return the current page snapshot."""

        from playwright.sync_api import Error as PlaywrightError

        page = self._new_navigation_page()
        logger.info("Navigating browser to %s", url)
        try:
            page.goto(url, wait_until="domcontentloaded")
        except PlaywrightError as exc:
            if self._is_download_triggered_navigation_error(exc):
                raise BrowserDownloadTriggeredError(url) from exc
            raise BrowserNavigationError(url, str(exc)) from exc
        snapshot = self._capture_snapshot(page, requested_url=url)
        logger.info(
            "Captured snapshot requested=%s final=%s title=%r intervention=%s",
            url,
            snapshot.final_url,
            snapshot.title,
            snapshot.intervention_reason.value
            if snapshot.intervention_reason is not None
            else "none",
        )
        return snapshot

    def current_snapshot(self) -> BrowserPageSnapshot:
        """Capture the current page without changing navigation."""

        page = self._active_page()
        requested_url = self._last_snapshot.requested_url or str(page.url)
        previous_snapshot = self._last_snapshot
        snapshot = self._capture_snapshot(page, requested_url=requested_url)
        if snapshot != previous_snapshot:
            logger.info(
                "Refreshed current snapshot final=%s title=%r intervention=%s",
                snapshot.final_url,
                snapshot.title,
                snapshot.intervention_reason.value
                if snapshot.intervention_reason is not None
                else "none",
            )
        return snapshot

    def wait_for_resume(
        self,
        should_cancel: Callable[[], bool],
        *,
        poll_interval_seconds: float = 0.5,
    ) -> bool:
        """Wait until the workflow is resumed or cancelled."""

        self._resume_event.clear()
        logger.info("Waiting for browser resume or blocker resolution")
        while not should_cancel():
            if self._resume_event.wait(timeout=poll_interval_seconds):
                logger.info("Browser resume signal received")
                return True
            if self.current_snapshot().intervention_reason is None:
                logger.info("Browser blocker cleared without explicit resume")
                return True
        logger.info("Browser wait loop cancelled before resume")
        return False

    def resume(self) -> None:
        """Resume the browser workflow after manual intervention."""

        logger.info("Manual browser resume requested")
        self._resume_event.set()

    def download_file(self, url: str, destination_dir: Path) -> Path | None:
        """Capture one browser-managed download when available."""

        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        destination_dir.mkdir(parents=True, exist_ok=True)
        page = self._active_page()
        logger.info("Attempting browser-managed download from %s", url)
        navigation_response: Response | None = None
        try:
            with page.expect_download(
                timeout=_DOWNLOAD_EVENT_TIMEOUT_MS
            ) as download_info:
                try:
                    navigation_response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                    )
                except PlaywrightError as exc:
                    if not self._is_download_triggered_navigation_error(exc):
                        raise
            download: Download = download_info.value
            suggested_name = (
                str(download.suggested_filename or "").strip() or "download.pdf"
            )
            target_path = destination_dir / suggested_name
            download.save_as(str(target_path))
            logger.info("Browser-managed download saved to %s", target_path)
            return target_path
        except PlaywrightTimeoutError:
            if navigation_response is None:
                logger.info("Browser download timed out without a navigation response")
                return None
            content_type = str(
                navigation_response.headers.get("content-type", "")
            ).lower()
            final_url = str(navigation_response.url)
            if "pdf" not in content_type and not looks_like_pdf_url(final_url):
                logger.info(
                    "Browser navigation completed without a download event or PDF "
                    "response final_url=%s content_type=%s",
                    final_url,
                    content_type or "unknown",
                )
                return None
            target_path = destination_dir / _inline_pdf_filename(
                navigation_response,
                url,
            )
            target_path.write_bytes(navigation_response.body())
            logger.info("Saved inline PDF browser response to %s", target_path)
            return target_path

    def http_request_state(
        self,
        url: str,
        *,
        referer: str | None = None,
    ) -> BrowserHttpRequestState:
        """Build browser-like headers and cookies for one HTTP request."""

        headers = {
            "User-Agent": self._browser_like_user_agent(),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": _accept_language_header(self._locale),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": _sec_fetch_site_value(url, referer),
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            headers["Referer"] = referer
        return BrowserHttpRequestState(headers, self._cookies_for_url(url))

    def close(self) -> None:
        """Close the Playwright context and stop the runtime."""

        if self._context is not None:
            logger.info("Closing Playwright browser context")
            self._context.close()
            self._context = None
            self._page = None
        if self._playwright is not None:
            logger.info("Stopping Playwright runtime")
            self._playwright.stop()
            self._playwright = None
        self._browser_user_agent = None
