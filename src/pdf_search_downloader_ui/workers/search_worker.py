"""Background worker that drives provider scraping and PDF downloads."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from ..browser_session import (
    BrowserDownloadTriggeredError,
    BrowserNavigationError,
    BrowserSessionManager,
    BrowserSessionProtocol,
)
from ..config import AppConfig
from ..html_tools import looks_like_pdf_url
from ..models import (
    BrowserPageSnapshot,
    DownloadOutcome,
    ManualInterventionReason,
    PdfCandidate,
    RunState,
    RunStatus,
    SearchHit,
    SearchRequest,
)
from ..persistence.manifest import ManifestStore
from ..providers import build_provider_map
from ..runtime_paths import manifest_database_path, temp_download_dir
from ..services.downloads import PdfDownloader

logger = logging.getLogger(__name__)

BrowserFactory = Callable[[AppConfig], BrowserSessionProtocol]

if TYPE_CHECKING:
    from ..providers.base import SearchProvider


def _default_browser_factory(config: AppConfig) -> BrowserSessionProtocol:
    """Build the default browser session for one app config."""

    return BrowserSessionManager(
        config.browser.profile_dir,
        locale=config.search.default_language,
        browser_download_dir=config.downloads.output_dir,
    )


class SearchRunWorker(QThread):
    """Run the bulk search and download workflow in the background."""

    _blocked_retry_timeout_seconds = 5.0
    _blocked_retry_poll_interval_seconds = 0.5

    progress_changed = Signal(str)
    hit_discovered = Signal(object)
    download_completed = Signal(object)
    manual_intervention_required = Signal(str, str, str)
    run_finished = Signal(object)

    def __init__(
        self,
        request: SearchRequest,
        config: AppConfig,
        *,
        browser_factory: BrowserFactory = _default_browser_factory,
    ) -> None:
        super().__init__()
        self._request = request
        self._config = config
        self._browser_factory = browser_factory
        self._browser_session: BrowserSessionProtocol | None = None

    def resume_browser(self) -> None:
        """Resume the browser flow after manual user intervention."""

        if self._browser_session is None:
            return
        self._browser_session.resume()

    def _cancelled(self) -> bool:
        """Return whether the worker has been asked to stop."""

        return self.isInterruptionRequested()

    def _fetch_ready_html(
        self,
        browser_session: BrowserSessionProtocol,
        url: str,
        provider: SearchProvider,
        state: RunState,
    ) -> str:
        """Load one page and block until it is ready for scraping."""

        logger.info(
            "Fetching page provider=%s url=%s",
            provider.provider_id.value,
            url,
        )
        snapshot = browser_session.fetch_snapshot(url)
        while snapshot.intervention_reason is not None:
            state.status = RunStatus.WAITING
            state.interventions.append(snapshot.intervention_reason)
            if snapshot.intervention_reason is ManualInterventionReason.BLOCKED:
                snapshot = self._wait_for_blocked_page_recovery(
                    browser_session,
                    requested_url=url,
                    snapshot=snapshot,
                    provider=provider,
                )
                continue
            logger.info(
                "Manual intervention required provider=%s reason=%s url=%s",
                provider.provider_id.value,
                snapshot.intervention_reason.value,
                snapshot.final_url,
            )
            self.progress_changed.emit(
                "Solve the "
                f"{snapshot.intervention_reason.value} step in the "
                f"{provider.display_name} browser window."
            )
            self.manual_intervention_required.emit(
                provider.provider_id.value,
                snapshot.intervention_reason.value,
                snapshot.final_url,
            )
            if not browser_session.wait_for_resume(self._cancelled):
                raise RuntimeError(
                    "Search run cancelled while waiting for manual resume."
                )
            self.progress_changed.emit(
                f"Rechecking {provider.display_name} after the browser step."
            )
            snapshot = browser_session.current_snapshot()
        logger.info(
            "Page ready provider=%s final_url=%s title=%r",
            provider.provider_id.value,
            snapshot.final_url,
            snapshot.title,
        )
        state.status = RunStatus.RUNNING
        return snapshot.html

    def _wait_for_blocked_page_recovery(
        self,
        browser_session: BrowserSessionProtocol,
        *,
        requested_url: str,
        snapshot: BrowserPageSnapshot,
        provider: SearchProvider,
    ) -> BrowserPageSnapshot:
        """Wait briefly for one blocked page to recover before skipping it."""

        logger.info(
            "Blocked page detected provider=%s url=%s final_url=%s",
            provider.provider_id.value,
            requested_url,
            snapshot.final_url,
        )
        self.progress_changed.emit(
            f"{provider.display_name} looks blocked. Waiting briefly before skipping."
        )
        deadline = time.monotonic() + self._blocked_retry_timeout_seconds
        latest_snapshot = snapshot
        while time.monotonic() < deadline:
            if self._cancelled():
                raise RuntimeError(
                    "Search run cancelled while waiting for a blocked page."
                )
            time.sleep(self._blocked_retry_poll_interval_seconds)
            latest_snapshot = browser_session.current_snapshot()
            if latest_snapshot.intervention_reason is None:
                logger.info(
                    "Blocked page cleared provider=%s final_url=%s",
                    provider.provider_id.value,
                    latest_snapshot.final_url,
                )
                return latest_snapshot
        raise BrowserNavigationError(
            requested_url,
            "Page remained blocked after a short recovery wait.",
        )

    def _resolve_candidate_for_hit(
        self,
        browser_session: BrowserSessionProtocol,
        provider: SearchProvider,
        state: RunState,
        hit: SearchHit,
    ) -> PdfCandidate | None:
        """Resolve one PDF candidate from the hit or its landing page."""

        if looks_like_pdf_url(hit.url):
            return provider.resolve_pdf_candidate(hit, "", hit.url)
        try:
            landing_html = self._fetch_ready_html(
                browser_session,
                hit.url,
                provider,
                state,
            )
        except BrowserDownloadTriggeredError:
            logger.info(
                "Treating browser-triggered download navigation as a PDF candidate "
                "source_url=%s",
                hit.url,
            )
            return PdfCandidate(
                source_url=hit.url,
                download_url=hit.url,
                filename_hint=hit.title,
                requires_browser_download=True,
            )
        except BrowserNavigationError as exc:
            logger.info(
                "Skipping hit because the landing page navigation failed "
                "source_url=%s details=%s",
                hit.url,
                exc.details,
            )
            return None
        return provider.resolve_pdf_candidate(hit, landing_html, hit.url)

    def run(self) -> None:
        """Execute the provider search and download workflow."""

        state = RunState(status=RunStatus.RUNNING)
        provider_map = build_provider_map()
        manifest_store = ManifestStore(manifest_database_path())
        downloader = PdfDownloader(
            manifest_store,
            temp_dir=temp_download_dir(),
            timeout_seconds=self._config.downloads.timeout_seconds,
        )
        browser_session = self._browser_factory(self._config)
        self._browser_session = browser_session
        downloads_seen = 0

        try:
            logger.info(
                "Search run started providers=%s query=%r max_pages=%s max_results=%s",
                [provider_id.value for provider_id in self._request.providers],
                self._request.query,
                self._request.max_pages,
                self._request.max_results,
            )
            for provider_id in self._request.providers:
                provider = provider_map[provider_id]
                logger.info("Provider loop started provider=%s", provider_id.value)
                for page_number in range(self._request.max_pages):
                    if self._cancelled():
                        state.status = RunStatus.CANCELLED
                        state.last_message = "Search run cancelled."
                        logger.info("Search run cancelled before provider page load")
                        self.run_finished.emit(state)
                        return

                    search_url = provider.build_search_url(self._request, page_number)
                    self.progress_changed.emit(
                        f"Loading {provider.display_name} page {page_number + 1}."
                    )
                    html = self._fetch_ready_html(
                        browser_session,
                        search_url,
                        provider,
                        state,
                    )
                    hits = provider.collect_hits(
                        html,
                        search_url,
                        page_number=page_number,
                        max_results=max(1, self._request.max_results - downloads_seen),
                    )
                    logger.info(
                        "Collected hits provider=%s page=%s count=%s",
                        provider_id.value,
                        page_number + 1,
                        len(hits),
                    )
                    if not hits:
                        logger.info(
                            "Stopping provider pagination because no hits were found"
                        )
                        break

                    for hit in hits:
                        if self._cancelled():
                            state.status = RunStatus.CANCELLED
                            state.last_message = "Search run cancelled."
                            logger.info("Search run cancelled during hit processing")
                            self.run_finished.emit(state)
                            return
                        if downloads_seen >= self._request.max_results:
                            break

                        state.hits_seen += 1
                        self.hit_discovered.emit(hit)
                        self.progress_changed.emit(
                            f"Resolving PDF candidate for {hit.title}."
                        )

                        candidate = self._resolve_candidate_for_hit(
                            browser_session,
                            provider,
                            state,
                            hit,
                        )
                        if candidate is None:
                            logger.info(
                                "No PDF candidate resolved source_url=%s",
                                hit.url,
                            )
                            continue

                        self.progress_changed.emit(
                            f"Downloading PDF for {hit.title}."
                        )
                        record = downloader.download_candidate(
                            hit,
                            candidate,
                            output_dir=self._request.output_dir,
                            skip_duplicates=self._request.skip_duplicates,
                            browser_session=browser_session,
                        )
                        self.download_completed.emit(record)
                        downloads_seen += 1
                        if record.outcome is DownloadOutcome.DOWNLOADED:
                            state.downloads_completed += 1
                            self.progress_changed.emit(
                                f"Downloaded {record.output_path.name}."
                                if record.output_path is not None
                                else f"Downloaded {hit.title}."
                            )
                        elif record.outcome is DownloadOutcome.SKIPPED:
                            state.duplicates_skipped += 1
                            self.progress_changed.emit(
                                f"Skipped duplicate for {hit.title}."
                            )
                        else:
                            state.failures += 1
                            self.progress_changed.emit(
                                f"Failed to download {hit.title}."
                            )
                        logger.info(
                            "Download outcome provider=%s "
                            "source_url=%s outcome=%s final_url=%s",
                            hit.provider_id.value,
                            hit.url,
                            record.outcome.value,
                            record.final_url,
                        )

                    if downloads_seen >= self._request.max_results:
                        break

            state.status = RunStatus.COMPLETED
            state.last_message = (
                "Completed search run."
                if state.failures == 0
                else "Completed search run with failures."
            )
            logger.info(
                "Search run completed downloads=%s skipped=%s failures=%s",
                state.downloads_completed,
                state.duplicates_skipped,
                state.failures,
            )
            self.run_finished.emit(state)
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            logger.exception("Search run failed")
            state.status = RunStatus.FAILED
            state.failures += 1
            state.last_message = str(exc)
            self.run_finished.emit(state)
        finally:
            downloader.close()
            if isinstance(browser_session, BrowserSessionManager):
                browser_session.close()
