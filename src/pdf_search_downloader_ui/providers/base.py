"""Base provider interfaces and shared parsing utilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol
from urllib.parse import parse_qs, quote_plus, urlparse

from ..html_tools import extract_links, extract_pdf_links, looks_like_pdf_url
from ..models import PdfCandidate, ProviderId, SearchHit, SearchRequest


class SearchProvider(Protocol):
    """Describe the provider interface used by the worker."""

    provider_id: ProviderId
    display_name: str

    def build_search_url(self, request: SearchRequest, page_number: int) -> str:
        """Build one provider search URL."""

        ...

    def collect_hits(
        self,
        html: str,
        page_url: str,
        *,
        page_number: int,
        max_results: int,
    ) -> list[SearchHit]:
        """Collect search hits from one provider result page."""

        ...

    def resolve_pdf_candidate(
        self,
        hit: SearchHit,
        landing_html: str,
        landing_url: str,
    ) -> PdfCandidate | None:
        """Resolve one PDF candidate from a result or landing page."""

        ...


class ProviderBase(ABC):
    """Shared implementation for live search providers."""

    provider_id: ProviderId
    display_name: str
    _internal_domains: tuple[str, ...]
    _blocked_domains: tuple[str, ...] = ("youtube.com", "www.youtube.com", "youtu.be")

    def _query_text(self, query: str) -> str:
        """Build the provider query text with the forced PDF filter."""

        base = " ".join(query.split())
        return f"{base} filetype:pdf".strip()

    def _encoded_query(self, query: str) -> str:
        """URL-encode one provider query."""

        return quote_plus(self._query_text(query))

    def _is_internal_url(self, url: str) -> bool:
        """Return whether one URL points back to the provider shell."""

        netloc = urlparse(url).netloc.lower()
        return any(domain in netloc for domain in self._internal_domains)

    def _is_blocked_url(self, url: str) -> bool:
        """Return whether one URL should never be opened as a hit."""

        netloc = urlparse(url).netloc.lower()
        return any(blocked_domain in netloc for blocked_domain in self._blocked_domains)

    def _normalize_result_url(self, url: str) -> str:
        """Normalize one provider result URL."""

        return url.strip()

    def collect_hits(
        self,
        html: str,
        page_url: str,
        *,
        page_number: int,
        max_results: int,
    ) -> list[SearchHit]:
        """Collect result links using lightweight HTML heuristics."""

        hits: list[SearchHit] = []
        seen_urls: set[str] = set()
        for link in extract_links(html, page_url):
            normalized_url = self._normalize_result_url(link.url)
            if not normalized_url.startswith(("http://", "https://")):
                continue
            if self._is_internal_url(normalized_url):
                continue
            if self._is_blocked_url(normalized_url):
                continue
            title = " ".join(link.text.split())
            if not title and not looks_like_pdf_url(normalized_url):
                continue
            if len(title) < 4 and not looks_like_pdf_url(normalized_url):
                continue
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            hits.append(
                SearchHit(
                    provider_id=self.provider_id,
                    title=title or normalized_url,
                    url=normalized_url,
                    display_url=urlparse(normalized_url).netloc,
                    snippet="",
                    rank=len(hits) + 1,
                    page_number=page_number,
                )
            )
            if len(hits) >= max_results:
                break
        return hits

    def resolve_pdf_candidate(
        self,
        hit: SearchHit,
        landing_html: str,
        landing_url: str,
    ) -> PdfCandidate | None:
        """Resolve one direct or one-hop PDF candidate."""

        if looks_like_pdf_url(hit.url):
            return PdfCandidate(
                source_url=hit.url,
                download_url=hit.url,
                filename_hint=hit.title,
            )
        pdf_links = extract_pdf_links(landing_html, landing_url)
        if not pdf_links:
            return None
        resolved_url = pdf_links[0]
        return PdfCandidate(
            source_url=hit.url,
            download_url=resolved_url,
            filename_hint=hit.title,
            requires_browser_download=False,
        )

    @abstractmethod
    def build_search_url(self, request: SearchRequest, page_number: int) -> str:
        """Build one provider-specific search URL."""


def unwrap_google_result_url(raw_url: str) -> str:
    """Unwrap one Google redirect URL when present."""

    parsed = urlparse(raw_url)
    if parsed.path != "/url":
        return raw_url
    params = parse_qs(parsed.query)
    query_target = params.get("q", [""])[0].strip()
    return query_target or raw_url
