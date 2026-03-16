"""Typed runtime configuration backed by ``threep-commons`` QSettings helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from threep_commons.paths import resolve_app_data_dir
from threep_commons.settings import QSettingsValueStore, ensure_schema_defaults

from .constants import APP_IDENTITY, SETTINGS_APP_NAME
from .models import ProviderId, SearchRequest


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Store provider enablement flags."""

    google_enabled: bool
    bing_enabled: bool


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Store search defaults and hard limits."""

    default_language: str
    default_market: str
    max_pages: int
    max_results: int


@dataclass(frozen=True, slots=True)
class DownloadConfig:
    """Store download destination and duplicate policy."""

    output_dir: Path
    skip_duplicates: bool


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    """Store browser automation settings."""

    profile_dir: Path


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Store the full persisted runtime configuration."""

    providers: ProviderConfig
    search: SearchConfig
    downloads: DownloadConfig
    browser: BrowserConfig
    setup_completed: bool

    def enabled_providers(self) -> tuple[ProviderId, ...]:
        """Return the enabled providers in stable order."""

        providers: list[ProviderId] = []
        if self.providers.google_enabled:
            providers.append(ProviderId.GOOGLE)
        if self.providers.bing_enabled:
            providers.append(ProviderId.BING)
        return tuple(providers)

    def build_request(self, query: str) -> SearchRequest:
        """Build one search request from the persisted defaults."""

        return SearchRequest(
            query=query.strip(),
            providers=self.enabled_providers(),
            language=self.search.default_language,
            market=self.search.default_market,
            max_pages=self.search.max_pages,
            max_results=self.search.max_results,
            output_dir=self.downloads.output_dir,
            skip_duplicates=self.downloads.skip_duplicates,
        )


def default_output_dir() -> Path:
    """Return the default PDF output directory."""

    path = resolve_app_data_dir(APP_IDENTITY) / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_profile_dir() -> Path:
    """Return the default Playwright profile directory."""

    path = resolve_app_data_dir(APP_IDENTITY) / "browser-profile"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_config() -> AppConfig:
    """Build the default application configuration."""

    return AppConfig(
        providers=ProviderConfig(google_enabled=True, bing_enabled=True),
        search=SearchConfig(
            default_language="it",
            default_market="it",
            max_pages=2,
            max_results=20,
        ),
        downloads=DownloadConfig(
            output_dir=default_output_dir(),
            skip_duplicates=True,
        ),
        browser=BrowserConfig(profile_dir=default_profile_dir()),
        setup_completed=False,
    )


def _schema(defaults: AppConfig) -> tuple[tuple[str, type[object], object], ...]:
    """Build the schema tuples consumed by the shared settings helper."""

    return (
        ("config/providers/google_enabled", bool, defaults.providers.google_enabled),
        ("config/providers/bing_enabled", bool, defaults.providers.bing_enabled),
        ("config/search/default_language", str, defaults.search.default_language),
        ("config/search/default_market", str, defaults.search.default_market),
        ("config/search/max_pages", int, defaults.search.max_pages),
        ("config/search/max_results", int, defaults.search.max_results),
        ("config/downloads/output_dir", str, str(defaults.downloads.output_dir)),
        (
            "config/downloads/skip_duplicates",
            bool,
            defaults.downloads.skip_duplicates,
        ),
        ("config/browser/profile_dir", str, str(defaults.browser.profile_dir)),
        ("prefs/setup_completed", bool, defaults.setup_completed),
        ("prefs/last_query", str, ""),
    )


def _new_config_store() -> QSettingsValueStore:
    """Build one QSettings store scoped to this application."""

    return QSettingsValueStore.from_identity(
        APP_IDENTITY,
        app_name=SETTINGS_APP_NAME,
    )


def config_store_file_path() -> str:
    """Return the canonical on-disk QSettings INI path."""

    return _new_config_store().file_name()


def ensure_config_exists() -> None:
    """Seed missing configuration keys into the shared settings store."""

    ensure_schema_defaults(_new_config_store(), _schema(get_default_config()))


def _clamp_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
    """Clamp one integer-like value to the supplied range."""

    if isinstance(value, bool):
        numeric = int(value)
    elif isinstance(value, int):
        numeric = value
    elif isinstance(value, float):
        numeric = int(value)
    elif isinstance(value, str):
        try:
            numeric = int(value)
        except ValueError:
            return fallback
    else:
        return fallback
    return max(minimum, min(maximum, numeric))


def load_config() -> AppConfig:
    """Load and normalize persisted configuration."""

    defaults = get_default_config()
    ensure_config_exists()
    store = _new_config_store()
    return AppConfig(
        providers=ProviderConfig(
            google_enabled=store.get_bool(
                "config/providers/google_enabled",
                defaults.providers.google_enabled,
            ),
            bing_enabled=store.get_bool(
                "config/providers/bing_enabled",
                defaults.providers.bing_enabled,
            ),
        ),
        search=SearchConfig(
            default_language=(
                str(
                    store.value(
                        "config/search/default_language",
                        defaults.search.default_language,
                    )
                ).strip()
                or defaults.search.default_language
            ),
            default_market=(
                str(
                    store.value(
                        "config/search/default_market",
                        defaults.search.default_market,
                    )
                ).strip()
                or defaults.search.default_market
            ),
            max_pages=_clamp_int(
                store.value("config/search/max_pages", defaults.search.max_pages),
                1,
                10,
                defaults.search.max_pages,
            ),
            max_results=_clamp_int(
                store.value("config/search/max_results", defaults.search.max_results),
                1,
                200,
                defaults.search.max_results,
            ),
        ),
        downloads=DownloadConfig(
            output_dir=Path(
                str(
                    store.value(
                        "config/downloads/output_dir",
                        str(defaults.downloads.output_dir),
                    )
                )
            ),
            skip_duplicates=store.get_bool(
                "config/downloads/skip_duplicates",
                defaults.downloads.skip_duplicates,
            ),
        ),
        browser=BrowserConfig(
            profile_dir=Path(
                str(
                    store.value(
                        "config/browser/profile_dir",
                        str(defaults.browser.profile_dir),
                    )
                )
            ),
        ),
        setup_completed=store.get_bool(
            "prefs/setup_completed",
            defaults.setup_completed,
        ),
    )


def save_config(config: AppConfig) -> None:
    """Persist the supplied configuration into QSettings."""

    store = _new_config_store()
    store.set_value("config/providers/google_enabled", config.providers.google_enabled)
    store.set_value("config/providers/bing_enabled", config.providers.bing_enabled)
    store.set_value("config/search/default_language", config.search.default_language)
    store.set_value("config/search/default_market", config.search.default_market)
    store.set_value("config/search/max_pages", config.search.max_pages)
    store.set_value("config/search/max_results", config.search.max_results)
    store.set_value("config/downloads/output_dir", str(config.downloads.output_dir))
    store.set_value(
        "config/downloads/skip_duplicates",
        config.downloads.skip_duplicates,
    )
    store.set_value("config/browser/profile_dir", str(config.browser.profile_dir))
    store.set_value("prefs/setup_completed", config.setup_completed)
    store.sync()


def last_query() -> str:
    """Return the last query text entered in the UI."""

    ensure_config_exists()
    value = _new_config_store().value("prefs/last_query", "")
    return str(value or "").strip()


def set_last_query(query: str) -> None:
    """Persist the last query entered by the user."""

    store = _new_config_store()
    store.set_value("prefs/last_query", query.strip())
    store.sync()
