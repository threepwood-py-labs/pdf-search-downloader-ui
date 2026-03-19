"""Common locale presets for the search UI and providers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocaleOption:
    """Describe one UI locale preset and provider host mapping."""

    label: str
    language: str
    market: str
    google_host: str
    bing_host: str


COMMON_LOCALE_OPTIONS: tuple[LocaleOption, ...] = (
    LocaleOption(
        label="Italian",
        language="it",
        market="it",
        google_host="www.google.it",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="English (US)",
        language="en-US",
        market="us",
        google_host="www.google.com",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="English (UK)",
        language="en-GB",
        market="uk",
        google_host="www.google.co.uk",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="German",
        language="de",
        market="de",
        google_host="www.google.de",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="French",
        language="fr",
        market="fr",
        google_host="www.google.fr",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="Spanish",
        language="es",
        market="es",
        google_host="www.google.es",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="Portuguese (Portugal)",
        language="pt",
        market="pt",
        google_host="www.google.pt",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="Portuguese (Brazil)",
        language="pt-BR",
        market="br",
        google_host="www.google.com.br",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="Dutch",
        language="nl",
        market="nl",
        google_host="www.google.nl",
        bing_host="www.bing.com",
    ),
    LocaleOption(
        label="Polish",
        language="pl",
        market="pl",
        google_host="www.google.pl",
        bing_host="www.bing.com",
    ),
)
DEFAULT_LOCALE_OPTION = COMMON_LOCALE_OPTIONS[0]
COMMON_LOCALE_LABELS: tuple[str, ...] = tuple(
    option.label for option in COMMON_LOCALE_OPTIONS
)


def locale_option_for_codes(language: str, market: str) -> LocaleOption:
    """Return the best-matching locale preset for stored language and market."""

    normalized_language = language.strip().lower()
    normalized_market = market.strip().lower()
    for option in COMMON_LOCALE_OPTIONS:
        if (
            option.language.lower() == normalized_language
            and option.market.lower() == normalized_market
        ):
            return option
    for option in COMMON_LOCALE_OPTIONS:
        if option.language.lower() == normalized_language:
            return option
    return DEFAULT_LOCALE_OPTION


def locale_option_for_label(label: str) -> LocaleOption:
    """Return the locale preset selected from the UI."""

    normalized_label = label.strip().lower()
    for option in COMMON_LOCALE_OPTIONS:
        if option.label.lower() == normalized_label:
            return option
    return DEFAULT_LOCALE_OPTION
