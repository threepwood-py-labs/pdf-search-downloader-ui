"""Bing search provider adapter."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

from ..locale_options import locale_option_for_codes
from ..models import ProviderId, SearchRequest
from .base import ProviderBase


def _decode_bing_redirect_token(raw_token: str) -> str | None:
    """Decode one Bing redirect token when it contains the target URL."""

    token = raw_token.strip()
    if token.startswith(("http://", "https://")):
        return token
    if token.startswith("a1"):
        token = token[2:]
    if not token:
        return None
    padded_token = f"{token}{'=' * (-len(token) % 4)}"
    try:
        decoded = base64.urlsafe_b64decode(padded_token).decode("utf-8").strip()
    except (UnicodeDecodeError, ValueError):
        return None
    if decoded.startswith(("http://", "https://")):
        return decoded
    return None


def unwrap_bing_result_url(raw_url: str) -> str:
    """Unwrap one Bing redirect URL into the final target when possible."""

    parsed = urlparse(raw_url)
    if "bing.com" not in parsed.netloc.lower():
        return raw_url
    params = parse_qs(parsed.query)
    for param_name in ("url", "u", "r"):
        param_value = params.get(param_name, [""])[0].strip()
        if not param_value:
            continue
        resolved = _decode_bing_redirect_token(param_value)
        if resolved is not None:
            return resolved
    return raw_url


class BingProvider(ProviderBase):
    """Collect PDF search hits from Bing result pages."""

    provider_id = ProviderId.BING
    display_name = "Bing"
    _internal_domains = ("bing.", "microsoft.com")

    def build_search_url(self, request: SearchRequest, page_number: int) -> str:
        """Build one Bing search URL using the configured locale."""

        first_result = (page_number * 10) + 1
        locale_option = locale_option_for_codes(request.language, request.market)
        return (
            f"https://{locale_option.bing_host}/search"
            f"?setlang={request.language}"
            f"&cc={request.market.upper()}"
            "&count=10"
            f"&first={first_result}"
            f"&q={self._encoded_query(request.query)}"
        )

    def _normalize_result_url(self, url: str) -> str:
        """Normalize one Bing result URL before filtering."""

        return unwrap_bing_result_url(url)
