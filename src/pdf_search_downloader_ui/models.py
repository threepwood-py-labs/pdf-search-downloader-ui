"""Typed domain models used by the PDF search downloader UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class ProviderId(StrEnum):
    """Identify one supported search provider."""

    GOOGLE = "google"
    BING = "bing"


class ManualInterventionReason(StrEnum):
    """Describe one browser state that requires user attention."""

    CAPTCHA = "captcha"
    CONSENT = "consent"
    CLOUDFLARE = "cloudflare"
    INTERSTITIAL = "interstitial"
    BLOCKED = "blocked"


class DownloadOutcome(StrEnum):
    """Describe the outcome of one attempted PDF download."""

    DOWNLOADED = "downloaded"
    SKIPPED = "skipped"
    FAILED = "failed"


class RunStatus(StrEnum):
    """Describe the current lifecycle state of one search run."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Describe one search request emitted from the UI."""

    query: str
    providers: tuple[ProviderId, ...]
    language: str
    market: str
    max_pages: int
    max_results: int
    output_dir: Path
    skip_duplicates: bool


@dataclass(frozen=True, slots=True)
class SearchHit:
    """Describe one search result collected from a provider page."""

    provider_id: ProviderId
    title: str
    url: str
    display_url: str
    snippet: str
    rank: int
    page_number: int


@dataclass(frozen=True, slots=True)
class PdfCandidate:
    """Describe one resolved PDF candidate ready for download."""

    source_url: str
    download_url: str
    filename_hint: str
    requires_browser_download: bool = False


@dataclass(frozen=True, slots=True)
class BrowserPageSnapshot:
    """Capture the browser state after loading one page."""

    requested_url: str
    final_url: str
    title: str
    html: str
    intervention_reason: ManualInterventionReason | None = None


@dataclass(frozen=True, slots=True)
class DownloadRecord:
    """Describe one download attempt persisted in the manifest."""

    provider_id: ProviderId
    title: str
    source_url: str
    final_url: str
    output_path: Path | None
    outcome: DownloadOutcome
    sha256_hex: str
    file_size_bytes: int
    message: str


@dataclass(slots=True)
class RunState:
    """Track progress while one worker run is active."""

    status: RunStatus = RunStatus.IDLE
    hits_seen: int = 0
    downloads_completed: int = 0
    duplicates_skipped: int = 0
    failures: int = 0
    last_message: str = ""
    interventions: list[ManualInterventionReason] = field(default_factory=list)
