"""Google search provider adapter."""

from __future__ import annotations

from ..locale_options import locale_option_for_codes
from ..models import ProviderId, SearchRequest
from .base import ProviderBase, unwrap_google_result_url


class GoogleProvider(ProviderBase):
    """Collect PDF search hits from Google result pages."""

    provider_id = ProviderId.GOOGLE
    display_name = "Google"
    _internal_domains = ("google.",)

    def build_search_url(self, request: SearchRequest, page_number: int) -> str:
        """Build one Google search URL using the configured locale."""

        start_index = page_number * 10
        locale_option = locale_option_for_codes(request.language, request.market)
        return (
            f"https://{locale_option.google_host}/search"
            f"?hl={request.language}"
            f"&gl={request.market}"
            "&num=10"
            f"&start={start_index}"
            f"&q={self._encoded_query(request.query)}"
        )

    def _normalize_result_url(self, url: str) -> str:
        """Unwrap Google redirect links before generic filtering."""

        return unwrap_google_result_url(url)
