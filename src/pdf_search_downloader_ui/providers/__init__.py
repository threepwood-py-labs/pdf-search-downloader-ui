"""Provider exports and factory helpers."""

from __future__ import annotations

from ..models import ProviderId
from .base import SearchProvider
from .bing import BingProvider
from .google import GoogleProvider


def build_provider_map() -> dict[ProviderId, SearchProvider]:
    """Build the provider registry used by the worker."""

    return {
        ProviderId.GOOGLE: GoogleProvider(),
        ProviderId.BING: BingProvider(),
    }


__all__ = ["SearchProvider", "build_provider_map"]
