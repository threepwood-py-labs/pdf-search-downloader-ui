"""Playwright-backed headed browser session management."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from .models import BrowserPageSnapshot, ManualInterventionReason

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from playwright.sync_api import BrowserContext, Download, Page, Playwright


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
        """Wait until the UI resumes the browser workflow."""

        ...

    def resume(self) -> None:
        """Resume a paused browser workflow."""

        ...

    def download_file(self, url: str, destination_dir: Path) -> Path | None:
        """Attempt to capture a browser-managed download."""

        ...


def detect_manual_intervention(
    html: str,
    final_url: str,
) -> ManualInterventionReason | None:
    """Detect whether one page requires manual user interaction."""

    text = f"{final_url}\n{html}".lower()
    if "captcha" in text or "unusual traffic" in text:
        return ManualInterventionReason.CAPTCHA
    if "before you continue" in text or "consent" in text:
        return ManualInterventionReason.CONSENT
    if "access denied" in text or "blocked" in text:
        return ManualInterventionReason.BLOCKED
    return None


class BrowserSessionManager(BrowserSessionProtocol):
    """Manage one visible persistent Playwright browser context."""

    def __init__(self, profile_dir: Path, *, locale: str) -> None:
        self._profile_dir = profile_dir
        self._locale = locale
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
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
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile_dir),
            headless=False,
            accept_downloads=True,
            locale=self._locale,
        )
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()

    def _active_page(self) -> Page:
        """Return the current active page after ensuring the context exists."""

        self._ensure_ready()
        if self._page is None:  # pragma: no cover - defensive invariant
            raise RuntimeError("Browser page is unavailable.")
        return self._page

    def fetch_snapshot(self, url: str) -> BrowserPageSnapshot:
        """Open one URL and return the current page snapshot."""

        page = self._active_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")
        html = page.content()
        final_url = str(page.url)
        snapshot = BrowserPageSnapshot(
            requested_url=url,
            final_url=final_url,
            title=str(page.title()),
            html=html,
            intervention_reason=detect_manual_intervention(html, final_url),
        )
        self._last_snapshot = snapshot
        return snapshot

    def current_snapshot(self) -> BrowserPageSnapshot:
        """Capture the current page without changing navigation."""

        page = self._active_page()
        html = page.content()
        final_url = str(page.url)
        snapshot = BrowserPageSnapshot(
            requested_url=self._last_snapshot.requested_url or final_url,
            final_url=final_url,
            title=str(page.title()),
            html=html,
            intervention_reason=detect_manual_intervention(html, final_url),
        )
        self._last_snapshot = snapshot
        return snapshot

    def wait_for_resume(
        self,
        should_cancel: Callable[[], bool],
        *,
        poll_interval_seconds: float = 0.1,
    ) -> bool:
        """Wait until the workflow is resumed or cancelled."""

        self._resume_event.clear()
        while not should_cancel():
            if self._resume_event.wait(timeout=poll_interval_seconds):
                return True
        return False

    def resume(self) -> None:
        """Resume the browser workflow after manual intervention."""

        self._resume_event.set()

    def download_file(self, url: str, destination_dir: Path) -> Path | None:
        """Capture one browser-managed download when available."""

        destination_dir.mkdir(parents=True, exist_ok=True)
        page = self._active_page()
        with page.expect_download() as download_info:
            page.goto(url, wait_until="domcontentloaded")
        download: Download = download_info.value
        suggested_name = (
            str(download.suggested_filename or "").strip() or "download.pdf"
        )
        target_path = destination_dir / suggested_name
        download.save_as(str(target_path))
        return target_path

    def close(self) -> None:
        """Close the Playwright context and stop the runtime."""

        if self._context is not None:
            self._context.close()
            self._context = None
            self._page = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
