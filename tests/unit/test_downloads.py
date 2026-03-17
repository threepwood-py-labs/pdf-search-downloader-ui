from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from pdf_search_downloader_ui.models import (
    DownloadOutcome,
    DownloadRecord,
    PdfCandidate,
    ProviderId,
    SearchHit,
)
from pdf_search_downloader_ui.persistence import ManifestStore
from pdf_search_downloader_ui.services import (
    PdfDownloader,
    is_pdf_signature,
    sanitize_filename,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_sanitize_filename_and_signature_detection() -> None:
    assert sanitize_filename('Comune: "Bilancio" / 2026') == "Comune Bilancio 2026"
    assert is_pdf_signature(b"%PDF-1.7 example") is True
    assert is_pdf_signature(b"not-a-pdf") is False


def test_manifest_roundtrip(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.sqlite3")
    record = DownloadRecord(
        provider_id=ProviderId.GOOGLE,
        title="Comune report",
        source_url="https://example.com/result",
        final_url="https://example.com/report.pdf",
        output_path=tmp_path / "report.pdf",
        outcome=DownloadOutcome.DOWNLOADED,
        sha256_hex="abc123",
        file_size_bytes=1024,
        message="ok",
    )

    store.record_download(record)
    loaded = store.find_existing(record.source_url, record.final_url)

    assert loaded == record


def test_pdf_downloader_skips_duplicate_manifest_entries(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.sqlite3")
    existing_output = tmp_path / "report.pdf"
    existing_output.write_bytes(b"%PDF-1.7 existing")
    existing_record = DownloadRecord(
        provider_id=ProviderId.GOOGLE,
        title="Comune report",
        source_url="https://example.com/result",
        final_url="https://example.com/report.pdf",
        output_path=existing_output,
        outcome=DownloadOutcome.DOWNLOADED,
        sha256_hex="abc123",
        file_size_bytes=existing_output.stat().st_size,
        message="ok",
    )
    store.record_download(existing_record)

    downloader = PdfDownloader(store, temp_dir=tmp_path / "temp")
    hit = SearchHit(
        provider_id=ProviderId.GOOGLE,
        title="Comune report",
        url="https://example.com/result",
        display_url="example.com",
        snippet="",
        rank=1,
        page_number=0,
    )
    candidate = PdfCandidate(
        source_url=hit.url,
        download_url="https://example.com/report.pdf",
        filename_hint="report.pdf",
    )

    result = downloader.download_candidate(
        hit,
        candidate,
        output_dir=tmp_path / "downloads",
        skip_duplicates=True,
    )
    downloader.close()

    assert result.outcome is DownloadOutcome.SKIPPED
    assert result.output_path == existing_output


def test_pdf_downloader_returns_failed_record_when_browser_fallback_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeBrowserSession:
        """Provide the minimal browser session contract for this test."""

        def download_file(self, url: str, destination_dir: Path) -> Path | None:
            del url, destination_dir
            raise RuntimeError("browser fallback timed out")

    store = ManifestStore(tmp_path / "manifest.sqlite3")
    downloader = PdfDownloader(store, temp_dir=tmp_path / "temp")
    hit = SearchHit(
        provider_id=ProviderId.GOOGLE,
        title="Comune report",
        url="https://example.com/result",
        display_url="example.com",
        snippet="",
        rank=1,
        page_number=0,
    )
    candidate = PdfCandidate(
        source_url=hit.url,
        download_url="https://example.com/report.pdf",
        filename_hint="report.pdf",
    )

    def raise_http_error(candidate: PdfCandidate) -> tuple[Path, str]:
        del candidate
        request = httpx.Request("GET", "https://example.com/report.pdf")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError(
            "Client error '403 Forbidden'",
            request=request,
            response=response,
        )

    monkeypatch.setattr(downloader, "_download_via_http", raise_http_error)

    result = downloader.download_candidate(
        hit,
        candidate,
        output_dir=tmp_path / "downloads",
        skip_duplicates=False,
        browser_session=FakeBrowserSession(),
    )
    downloader.close()

    assert result.outcome is DownloadOutcome.FAILED
    assert result.output_path is None
    assert "browser fallback" in result.message.lower()
