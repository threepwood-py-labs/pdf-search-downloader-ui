"""Command-line automation for topic-driven PDF search and validation."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import fitz
from threep_commons.logging import setup_logging_from_identity
from threep_commons.paths import configure_qsettings

from .browser_session import (
    BrowserDownloadTriggeredError,
    BrowserNavigationError,
    BrowserSessionManager,
)
from .config import (
    AppConfig,
    BrowserConfig,
    DownloadConfig,
    SearchConfig,
    ensure_config_exists,
    load_config,
)
from .constants import APP_IDENTITY
from .html_tools import looks_like_pdf_url
from .models import (
    DownloadOutcome,
    ManualInterventionReason,
    PdfCandidate,
    ProviderId,
    SearchHit,
    SearchRequest,
)
from .persistence.manifest import ManifestStore
from .providers import build_provider_map
from .runtime_paths import app_data_dir, temp_download_dir
from .services.downloads import PdfDownloader

if TYPE_CHECKING:
    from .browser_session import BrowserSessionProtocol
    from .providers import SearchProvider


logger = logging.getLogger(__name__)


SERIES_QUERY_SEEDS = (
    "numeri complessi piano di Gauss esercizi liceo pdf",
    "numeri complessi forma algebrica modulo coniugato esercizi pdf",
    "forma trigonometrica numeri complessi radici n-esime esercizi pdf",
    "radici dell'unita numeri complessi esercizi pdf",
    "equazioni in C numeri complessi esercizi pdf",
    "polinomi coefficienti reali radici complesse coniugate fattorizzazione pdf",
    "funzioni complesse parametro reale piano di Gauss esercizi pdf",
    "seno coseno complesso esponenziale cosh sinh identita iperbolica pdf",
)


VALIDATION_TERMS = {
    "piano_gauss": ("piano di gauss", "argand", "asse reale", "asse immaginario"),
    "algebra": (
        "numero complesso",
        "numeri complessi",
        "parte reale",
        "parte immaginaria",
        "coniugato",
        "modulo",
        "argomento",
    ),
    "trig_radici": (
        "forma trigonometrica",
        "forma polare",
        "radici n",
        "radici dell",
        "radici dell'unita",
        "de moivre",
    ),
    "equazioni": (
        "equazione",
        "equazioni",
        "soluzioni in c",
        "z^2",
        "z2",
    ),
    "polinomi": (
        "polinomio",
        "polinomi",
        "coefficienti reali",
        "radici complesse",
        "coniugate",
        "fattorizzazione",
    ),
    "funzioni": (
        "funzione",
        "parametro",
        "curva",
        "inversione",
        "reciproco",
    ),
    "iperboliche": (
        "seno",
        "coseno",
        "esponenziale",
        "cosh",
        "sinh",
        "iperbolic",
    ),
}


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Describe how well one PDF matches the target series topics."""

    path: str
    readable_pages: int
    chars: int
    score: int
    matched_categories: tuple[str, ...]
    matched_terms: tuple[str, ...]
    conformant: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AutoRecord:
    """Persist one automated search/download/validation row."""

    query: str
    provider: str
    title: str
    source_url: str
    final_url: str
    output_path: str
    outcome: str
    validation_score: int
    conformant: bool
    matched_categories: str
    matched_terms: str
    message: str


def normalize_text(text: str) -> str:
    """Normalize text for matching without changing downloaded files."""

    lowered = text.lower()
    lowered = lowered.replace("\x00", " ")
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered


def build_series_queries(extra_queries: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return the default target search queries plus user additions."""

    values: list[str] = []
    for query in (*SERIES_QUERY_SEEDS, *extra_queries):
        cleaned = " ".join(query.split())
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return tuple(values)


def extract_pdf_text(path: Path, *, max_pages: int) -> tuple[str, int]:
    """Extract native PDF text from up to ``max_pages`` pages."""

    chunks: list[str] = []
    readable_pages = 0
    with fitz.open(path) as doc:
        page_count = min(max_pages, doc.page_count)
        for page_index in range(page_count):
            text = doc[page_index].get_text("text").strip()
            if text:
                readable_pages += 1
                chunks.append(text)
    return "\n".join(chunks), readable_pages


def validate_pdf_path(
    path: Path,
    *,
    min_score: int = 6,
    max_pages: int = 20,
) -> ValidationResult:
    """Validate one PDF against the complex-number series topics."""

    try:
        raw_text, readable_pages = extract_pdf_text(path, max_pages=max_pages)
    except Exception as exc:
        return ValidationResult(
            path=str(path),
            readable_pages=0,
            chars=0,
            score=0,
            matched_categories=(),
            matched_terms=(),
            conformant=False,
            reason=f"PDF text extraction failed: {type(exc).__name__}: {exc}",
        )

    text = normalize_text(raw_text)
    matched_categories: list[str] = []
    matched_terms: list[str] = []
    score = 0
    for category, terms in VALIDATION_TERMS.items():
        category_hits = [term for term in terms if term in text]
        if not category_hits:
            continue
        matched_categories.append(category)
        matched_terms.extend(category_hits[:4])
        score += min(4, len(category_hits))

    has_core_complex_topic = any(
        category in matched_categories
        for category in ("piano_gauss", "algebra", "trig_radici", "polinomi")
    )
    conformant = score >= min_score and has_core_complex_topic
    reason = (
        "Matches target complex-number series topics."
        if conformant
        else "Insufficient topic overlap with the input series."
    )
    if readable_pages == 0:
        reason = "No native text found; cannot validate automatically."
    return ValidationResult(
        path=str(path),
        readable_pages=readable_pages,
        chars=len(raw_text),
        score=score,
        matched_categories=tuple(matched_categories),
        matched_terms=tuple(dict.fromkeys(matched_terms)),
        conformant=conformant,
        reason=reason,
    )


def _request_for_query(
    config: AppConfig,
    query: str,
    *,
    providers: tuple[ProviderId, ...],
    max_pages: int,
    max_results: int,
    output_dir: Path,
) -> SearchRequest:
    return SearchRequest(
        query=query,
        providers=providers,
        language=config.search.default_language,
        market=config.search.default_market,
        max_pages=max_pages,
        max_results=max_results,
        output_dir=output_dir,
        skip_duplicates=config.downloads.skip_duplicates,
    )


def _provider_ids(
    raw_values: tuple[str, ...],
    config: AppConfig,
) -> tuple[ProviderId, ...]:
    del config
    if not raw_values:
        return (ProviderId.GOOGLE,)
    providers: list[ProviderId] = []
    for raw in raw_values:
        provider = ProviderId(raw.lower())
        if provider is ProviderId.BING:
            raise ValueError("Auto mode is restricted to Google.")
        providers.append(provider)
    return tuple(dict.fromkeys(providers))


def _fetch_ready_html_auto(
    browser_session: BrowserSessionProtocol,
    url: str,
    provider: SearchProvider,
    *,
    manual_wait_seconds: float,
) -> str:
    """Fetch one URL, waiting briefly for recoverable manual-intervention pages."""

    snapshot = browser_session.fetch_snapshot(url)
    deadline = time.monotonic() + manual_wait_seconds
    while snapshot.intervention_reason is not None:
        reason = snapshot.intervention_reason
        message = (
            f"INTERVENTION {provider.display_name} {reason.value}: {snapshot.final_url}"
        )
        print(
            message,
            flush=True,
        )
        if reason is not ManualInterventionReason.BLOCKED:
            browser_session.bring_to_front()
        if time.monotonic() >= deadline:
            raise BrowserNavigationError(
                url,
                f"Manual intervention did not clear within {manual_wait_seconds}s.",
            )
        time.sleep(2.0)
        snapshot = browser_session.current_snapshot()
    return snapshot.html


def _resolve_candidate_auto(
    browser_session: BrowserSessionProtocol,
    provider: SearchProvider,
    hit: SearchHit,
    *,
    manual_wait_seconds: float,
) -> PdfCandidate | None:
    if looks_like_pdf_url(hit.url):
        return provider.resolve_pdf_candidate(hit, "", hit.url)
    try:
        landing_html = _fetch_ready_html_auto(
            browser_session,
            hit.url,
            provider,
            manual_wait_seconds=manual_wait_seconds,
        )
    except BrowserDownloadTriggeredError:
        return PdfCandidate(
            source_url=hit.url,
            download_url=hit.url,
            filename_hint=hit.title,
            requires_browser_download=True,
        )
    except BrowserNavigationError as exc:
        logger.info("Skipping landing page source_url=%s details=%s", hit.url, exc)
        return None
    return provider.resolve_pdf_candidate(hit, landing_html, hit.url)


def write_auto_reports(records: list[AutoRecord], report_dir: Path) -> None:
    """Write CSV and JSON automation reports."""

    report_dir.mkdir(parents=True, exist_ok=True)
    payload = [asdict(record) for record in records]
    (report_dir / "auto_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (report_dir / "auto_results.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(AutoRecord.__dataclass_fields__.keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(payload)


def run_auto(args: argparse.Namespace) -> int:
    """Run the automated search, download, and validation workflow."""

    configure_qsettings(APP_IDENTITY)
    app_data_dir().mkdir(parents=True, exist_ok=True)
    setup_logging_from_identity(APP_IDENTITY)
    ensure_config_exists()
    persisted = load_config()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        providers=persisted.providers,
        search=SearchConfig(
            default_language=args.language or persisted.search.default_language,
            default_market=args.market or persisted.search.default_market,
            max_pages=args.max_pages,
            max_results=args.max_results,
        ),
        downloads=DownloadConfig(
            output_dir=output_dir,
            skip_duplicates=not args.no_skip_duplicates,
            timeout_seconds=args.timeout_seconds,
        ),
        browser=BrowserConfig(
            profile_dir=Path(args.profile_dir).resolve()
            if args.profile_dir
            else persisted.browser.profile_dir
        ),
        setup_completed=True,
    )

    providers = _provider_ids(tuple(args.provider), config)
    queries = build_series_queries(tuple(args.query))
    provider_map = build_provider_map()
    manifest_store = ManifestStore(output_dir / "auto_manifest.sqlite3")
    downloader = PdfDownloader(
        manifest_store,
        temp_dir=temp_download_dir(),
        timeout_seconds=config.downloads.timeout_seconds,
    )
    browser_session = BrowserSessionManager(
        config.browser.profile_dir,
        locale=config.search.default_language,
        browser_download_dir=output_dir,
    )
    records: list[AutoRecord] = []
    seen_urls: set[str] = set()
    downloaded_or_skipped = 0

    try:
        for query in queries:
            if downloaded_or_skipped >= args.max_results:
                break
            request = _request_for_query(
                config,
                query,
                providers=providers,
                max_pages=args.max_pages,
                max_results=args.per_query_results,
                output_dir=output_dir,
            )
            print(f"QUERY {query}", flush=True)
            for provider_id in providers:
                provider = provider_map[provider_id]
                for page_number in range(request.max_pages):
                    if downloaded_or_skipped >= args.max_results:
                        break
                    search_url = provider.build_search_url(request, page_number)
                    try:
                        html = _fetch_ready_html_auto(
                            browser_session,
                            search_url,
                            provider,
                            manual_wait_seconds=args.manual_wait_seconds,
                        )
                    except BrowserNavigationError as exc:
                        print(f"SKIP_SEARCH {provider.display_name}: {exc}", flush=True)
                        break
                    hits = provider.collect_hits(
                        html,
                        search_url,
                        page_number=page_number,
                        max_results=args.per_query_results,
                    )
                    print(
                        f"HITS {provider.display_name} page={page_number + 1} "
                        f"count={len(hits)}",
                        flush=True,
                    )
                    for hit in hits:
                        if downloaded_or_skipped >= args.max_results:
                            break
                        if hit.url in seen_urls:
                            continue
                        seen_urls.add(hit.url)
                        candidate = _resolve_candidate_auto(
                            browser_session,
                            provider,
                            hit,
                            manual_wait_seconds=args.manual_wait_seconds,
                        )
                        if candidate is None:
                            continue
                        record = downloader.download_candidate(
                            hit,
                            candidate,
                            output_dir=output_dir,
                            skip_duplicates=config.downloads.skip_duplicates,
                            browser_session=browser_session,
                        )
                        if record.outcome in (
                            DownloadOutcome.DOWNLOADED,
                            DownloadOutcome.SKIPPED,
                        ):
                            downloaded_or_skipped += 1
                        validation = (
                            validate_pdf_path(
                                record.output_path,
                                min_score=args.min_score,
                                max_pages=args.validation_pages,
                            )
                            if record.output_path is not None
                            and record.output_path.exists()
                            else ValidationResult(
                                path="",
                                readable_pages=0,
                                chars=0,
                                score=0,
                                matched_categories=(),
                                matched_terms=(),
                                conformant=False,
                                reason="No downloaded file to validate.",
                            )
                        )
                        records.append(
                            AutoRecord(
                                query=query,
                                provider=provider_id.value,
                                title=record.title,
                                source_url=record.source_url,
                                final_url=record.final_url,
                                output_path=str(record.output_path or ""),
                                outcome=record.outcome.value,
                                validation_score=validation.score,
                                conformant=validation.conformant,
                                matched_categories=", ".join(
                                    validation.matched_categories
                                ),
                                matched_terms=", ".join(validation.matched_terms),
                                message=f"{record.message} {validation.reason}",
                            )
                        )
                        print(
                            "PDF "
                            f"{record.outcome.value} score={validation.score} "
                            f"conformant={validation.conformant} {record.title}",
                            flush=True,
                        )
    finally:
        write_auto_reports(records, output_dir / "auto_report")
        downloader.close()
        browser_session.close()

    conformant_count = sum(1 for record in records if record.conformant)
    print(f"REPORT {output_dir / 'auto_report'}", flush=True)
    print(f"CONFORMANT {conformant_count}/{len(records)}", flush=True)
    return 0 if conformant_count > 0 or not records else 2


def build_auto_parser() -> argparse.ArgumentParser:
    """Build the ``auto`` subcommand parser."""

    parser = argparse.ArgumentParser(
        prog="pdf-search-downloader-ui auto",
        description="Automatically search, download, and validate PDF material.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.cwd() / "auto_pdf_downloads"),
        help="Directory for downloaded PDFs and validation reports.",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Extra search query. May be repeated.",
    )
    parser.add_argument(
        "--provider",
        action="append",
        choices=[ProviderId.GOOGLE.value],
        default=[],
        help="Provider to use. Auto mode is restricted to Google.",
    )
    parser.add_argument("--language", default=None)
    parser.add_argument("--market", default=None)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-results", type=int, default=12)
    parser.add_argument("--per-query-results", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--manual-wait-seconds", type=float, default=45.0)
    parser.add_argument("--validation-pages", type=int, default=20)
    parser.add_argument("--min-score", type=int, default=6)
    parser.add_argument("--profile-dir", default=None)
    parser.add_argument("--no-skip-duplicates", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the auto mode command."""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = build_auto_parser()
    return run_auto(parser.parse_args(argv))
