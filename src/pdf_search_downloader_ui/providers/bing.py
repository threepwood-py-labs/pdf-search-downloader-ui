"""Bing search provider adapter."""

from __future__ import annotations

from ..models import ProviderId, SearchRequest
from .base import ProviderBase


class BingProvider(ProviderBase):
    """Collect PDF search hits from Bing result pages."""

    provider_id = ProviderId.BING
    display_name = "Bing"
    _internal_domains = ("bing.com", "microsoft.com")

    def build_search_url(self, request: SearchRequest, page_number: int) -> str:
        """Build one Bing search URL using the configured locale."""

        first_result = (page_number * 10) + 1
        return (
            "https://www.bing.com/search"
            f"?setlang={request.language}"
            f"&cc={request.market.upper()}"
            "&count=10"
            f"&first={first_result}"
            f"&q={self._encoded_query(request.query)}"
        )
