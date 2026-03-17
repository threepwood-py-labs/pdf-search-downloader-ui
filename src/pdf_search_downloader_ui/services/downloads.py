"""PDF download helpers and the main download service."""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from ..html_tools import looks_like_pdf_url
from ..models import DownloadOutcome, DownloadRecord, PdfCandidate, SearchHit

if TYPE_CHECKING:
    from ..browser_session import BrowserSessionProtocol
    from ..persistence.manifest import ManifestStore

_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def sanitize_filename(raw_title: str) -> str:
    """Normalize one title into a filesystem-safe stem."""

    cleaned = _INVALID_FILENAME_CHARS_RE.sub(" ", raw_title).strip()
    normalized = " ".join(cleaned.split())
    return normalized[:120] or "download"


def is_pdf_signature(content: bytes) -> bool:
    """Return whether the supplied bytes start with the PDF magic header."""

    return content.startswith(b"%PDF-")


def _hash_text(text: str) -> str:
    """Return a short deterministic hash for one text value."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _target_filename(
    title: str,
    filename_hint: str,
) -> str:
    """Build a deterministic output filename for one PDF candidate."""

    hinted_name = Path(filename_hint).name if filename_hint else ""
    if hinted_name.lower().endswith(".pdf"):
        base_name = sanitize_filename(Path(hinted_name).stem)
    else:
        base_name = sanitize_filename(title)
    return f"{base_name}.pdf"


class PdfDownloader:
    """Download PDF candidates with duplicate skipping and manifest updates."""

    def __init__(
        self,
        manifest_store: ManifestStore,
        *,
        temp_dir: Path,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._manifest_store = manifest_store
        self._temp_dir = temp_dir
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=timeout_seconds,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/145.0.0.0 Safari/537.36"
                )
            },
        )

    def close(self) -> None:
        """Close the internal HTTP client."""

        self._client.close()

    def _download_via_http(
        self,
        candidate: PdfCandidate,
        *,
        browser_session: BrowserSessionProtocol | None = None,
    ) -> tuple[Path, str]:
        """Download one PDF candidate through HTTP."""

        referer = (
            candidate.source_url
            if candidate.source_url != candidate.download_url
            else None
        )
        request_headers: dict[str, str] = {}
        request_cookies: dict[str, str] = {}
        if browser_session is not None:
            browser_request_state = browser_session.http_request_state(
                candidate.download_url,
                referer=referer,
            )
            request_headers = browser_request_state.headers
            request_cookies = browser_request_state.cookies
        else:
            request_headers = {
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,image/apng,*/*;q=0.8,"
                    "application/signed-exchange;v=b3;q=0.7"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
            }
            if referer is not None:
                request_headers["Referer"] = referer
        with self._client.stream(
            "GET",
            candidate.download_url,
            headers=request_headers,
            cookies=request_cookies,
        ) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(
                suffix=".pdf",
                prefix="pdf-search-",
                delete=False,
                dir=self._temp_dir,
            ) as handle:
                has_signature = False
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    if not has_signature and is_pdf_signature(chunk):
                        has_signature = True
                    handle.write(chunk)
                temp_path = Path(handle.name)

            content_type = str(response.headers.get("content-type", "")).lower()
            if "pdf" not in content_type and not has_signature:
                temp_path.unlink(missing_ok=True)
                raise ValueError("Response did not contain PDF content.")
            return temp_path, str(response.url)

    def _download_via_browser(
        self,
        browser_session: BrowserSessionProtocol,
        candidate: PdfCandidate,
    ) -> tuple[Path, str]:
        """Capture one browser download as a fallback path."""

        download_path = browser_session.download_file(
            candidate.download_url,
            self._temp_dir,
        )
        if download_path is None:
            raise ValueError("Browser download did not produce a file.")
        first_bytes = download_path.read_bytes()[:5]
        if not is_pdf_signature(first_bytes):
            download_path.unlink(missing_ok=True)
            raise ValueError("Browser-managed download was not a PDF.")
        return download_path, candidate.download_url

    def _materialize_output_path(
        self,
        output_dir: Path,
        hit: SearchHit,
        candidate: PdfCandidate,
        final_url: str,
    ) -> Path:
        """Resolve one unique output path for the downloaded file."""

        output_dir.mkdir(parents=True, exist_ok=True)
        candidate_name = _target_filename(hit.title, candidate.filename_hint)
        target_path = output_dir / candidate_name
        if not target_path.exists():
            return target_path
        host = urlparse(final_url).netloc or "file"
        suffix = _hash_text(final_url)
        return output_dir / f"{sanitize_filename(hit.title)}_{host}_{suffix}.pdf"

    def _build_record(
        self,
        hit: SearchHit,
        final_url: str,
        target_path: Path | None,
        outcome: DownloadOutcome,
        message: str,
    ) -> DownloadRecord:
        """Build one typed download record."""

        sha256_hex = ""
        file_size = 0
        if target_path is not None and target_path.exists():
            raw_bytes = target_path.read_bytes()
            sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
            file_size = len(raw_bytes)
        return DownloadRecord(
            provider_id=hit.provider_id,
            title=hit.title,
            source_url=hit.url,
            final_url=final_url,
            output_path=target_path,
            outcome=outcome,
            sha256_hex=sha256_hex,
            file_size_bytes=file_size,
            message=message,
        )

    def download_candidate(
        self,
        hit: SearchHit,
        candidate: PdfCandidate,
        *,
        output_dir: Path,
        skip_duplicates: bool,
        browser_session: BrowserSessionProtocol | None = None,
    ) -> DownloadRecord:
        """Download one candidate and persist it in the manifest when successful."""

        existing = self._manifest_store.find_existing(hit.url, candidate.download_url)
        if skip_duplicates and existing is not None:
            return DownloadRecord(
                provider_id=hit.provider_id,
                title=hit.title,
                source_url=hit.url,
                final_url=existing.final_url,
                output_path=existing.output_path,
                outcome=DownloadOutcome.SKIPPED,
                sha256_hex=existing.sha256_hex,
                file_size_bytes=existing.file_size_bytes,
                message="Skipped duplicate candidate already stored in manifest.",
            )

        try:
            temp_path, final_url = self._download_via_http(
                candidate,
                browser_session=browser_session,
            )
        except (httpx.HTTPError, ValueError):
            if browser_session is None:
                return self._build_record(
                    hit,
                    candidate.download_url,
                    None,
                    DownloadOutcome.FAILED,
                    "Failed to download PDF via HTTP.",
                )
            try:
                temp_path, final_url = self._download_via_browser(
                    browser_session,
                    candidate,
                )
            except Exception:
                return self._build_record(
                    hit,
                    candidate.download_url,
                    None,
                    DownloadOutcome.FAILED,
                    "Failed to download PDF via HTTP and browser fallback.",
                )

        if not looks_like_pdf_url(final_url) and not is_pdf_signature(
            temp_path.read_bytes()[:5]
        ):
            temp_path.unlink(missing_ok=True)
            return self._build_record(
                hit,
                final_url,
                None,
                DownloadOutcome.FAILED,
                "Resolved file did not validate as a PDF.",
            )

        target_path = self._materialize_output_path(
            output_dir,
            hit,
            candidate,
            final_url,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.replace(target_path)
        record = self._build_record(
            hit,
            final_url,
            target_path,
            DownloadOutcome.DOWNLOADED,
            "Downloaded PDF successfully.",
        )
        self._manifest_store.record_download(record)
        return record
