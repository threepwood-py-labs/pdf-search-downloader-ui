from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from threep_commons.paths import configure_qsettings

from pdf_search_downloader_ui.config import (
    AppConfig,
    BrowserConfig,
    DownloadConfig,
    ProviderConfig,
    SearchConfig,
    config_store_file_path,
    ensure_config_exists,
    get_default_config,
    load_config,
    save_config,
)
from pdf_search_downloader_ui.constants import (
    APP_IDENTITY,
    SETTINGS_APP_NAME,
    SETTINGS_ORG_NAME,
)


def _settings_file(config_dir: Path) -> Path:
    configure_qsettings(APP_IDENTITY, str(config_dir))
    settings = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_ORG_NAME,
        SETTINGS_APP_NAME,
    )
    settings.sync()
    return Path(settings.fileName())


def test_ensure_config_exists_seeds_expected_keys(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    path = _settings_file(config_dir)
    if path.exists():
        path.unlink()

    ensure_config_exists()
    settings = QSettings(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        SETTINGS_ORG_NAME,
        SETTINGS_APP_NAME,
    )
    keys = set(settings.allKeys())

    assert "config/providers/google_enabled" in keys
    assert "config/downloads/output_dir" in keys
    assert "prefs/setup_completed" in keys


def test_load_and_save_config_roundtrip(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    payload = AppConfig(
        providers=ProviderConfig(google_enabled=True, bing_enabled=False),
        search=SearchConfig(
            default_language="it",
            default_market="it",
            max_pages=4,
            max_results=12,
        ),
        downloads=DownloadConfig(
            output_dir=tmp_path / "downloads",
            skip_duplicates=False,
            timeout_seconds=33,
        ),
        browser=BrowserConfig(profile_dir=tmp_path / "profile"),
        setup_completed=True,
    )
    save_config(payload)
    loaded = load_config()

    assert loaded.providers.google_enabled is True
    assert loaded.providers.bing_enabled is False
    assert loaded.search.max_pages == 4
    assert loaded.search.max_results == 12
    assert loaded.downloads.output_dir == tmp_path / "downloads"
    assert loaded.downloads.timeout_seconds == 33
    assert loaded.browser.profile_dir == tmp_path / "profile"
    assert loaded.setup_completed is True


def test_config_store_file_path_matches_qsettings(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))

    expected = _settings_file(config_dir)

    assert Path(config_store_file_path()) == expected
    assert get_default_config().downloads.output_dir.name == "downloads"
