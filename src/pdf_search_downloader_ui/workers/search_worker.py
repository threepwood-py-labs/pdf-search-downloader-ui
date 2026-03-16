"""Background worker that drives provider scraping and PDF downloads."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, Signal

from ..browser_session import BrowserSessionManager, BrowserSessionProtocol
from ..config import AppConfig
from ..models import DownloadOutcome, RunState, RunStatus, SearchRequest
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
    )


class SearchRunWorker(QThread):
    """Run the bulk search and download workflow in the background."""

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

        snapshot = browser_session.fetch_snapshot(url)
        while snapshot.intervention_reason is not None:
            state.status = RunStatus.WAITING
            state.interventions.append(snapshot.intervention_reason)
            self.manual_intervention_required.emit(
                provider.provider_id.value,
                snapshot.intervention_reason.value,
                snapshot.final_url,
            )
            if not browser_session.wait_for_resume(self._cancelled):
                raise RuntimeError(
                    "Search run cancelled while waiting for manual resume."
                )
            snapshot = browser_session.current_snapshot()
        state.status = RunStatus.RUNNING
        return snapshot.html

    def run(self) -> None:
        """Execute the provider search and download workflow."""

        state = RunState(status=RunStatus.RUNNING)
        provider_map = build_provider_map()
        manifest_store = ManifestStore(manifest_database_path())
        downloader = PdfDownloader(manifest_store, temp_dir=temp_download_dir())
        browser_session = self._browser_factory(self._config)
        self._browser_session = browser_session
        downloads_seen = 0

        try:
            for provider_id in self._request.providers:
                provider = provider_map[provider_id]
                for page_number in range(self._request.max_pages):
                    if self._cancelled():
                        state.status = RunStatus.CANCELLED
                        state.last_message = "Search run cancelled."
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
                    if not hits:
                        break

                    for hit in hits:
                        if self._cancelled():
                            state.status = RunStatus.CANCELLED
                            state.last_message = "Search run cancelled."
                            self.run_finished.emit(state)
                            return
                        if downloads_seen >= self._request.max_results:
                            break

                        state.hits_seen += 1
                        self.hit_discovered.emit(hit)
                        self.progress_changed.emit(
                            f"Resolving PDF candidate for {hit.title}."
                        )

                        landing_html = ""
                        landing_url = hit.url
                        if not hit.url.lower().endswith(".pdf"):
                            landing_html = self._fetch_ready_html(
                                browser_session,
                                hit.url,
                                provider,
                                state,
                            )
                        candidate = provider.resolve_pdf_candidate(
                            hit,
                            landing_html,
                            landing_url,
                        )
                        if candidate is None:
                            continue

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
                        elif record.outcome is DownloadOutcome.SKIPPED:
                            state.duplicates_skipped += 1
                        else:
                            state.failures += 1

                    if downloads_seen >= self._request.max_results:
                        break

            state.status = RunStatus.COMPLETED
            state.last_message = (
                "Completed search run."
                if state.failures == 0
                else "Completed search run with failures."
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
