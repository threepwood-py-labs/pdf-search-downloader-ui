"""Runtime path helpers that route through ``threep-commons``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from threep_commons.files import resolve_runtime_file_path
from threep_commons.paths import resolve_app_data_dir

from .constants import APP_IDENTITY

if TYPE_CHECKING:
    from pathlib import Path


def app_data_dir() -> Path:
    """Return the application data directory."""

    return resolve_app_data_dir(APP_IDENTITY)


def manifest_database_path() -> Path:
    """Return the SQLite manifest database path."""

    return resolve_runtime_file_path(APP_IDENTITY, "manifest.sqlite3", subdir="data")


def temp_download_dir() -> Path:
    """Return the temporary download directory."""

    return resolve_runtime_file_path(APP_IDENTITY, "temp", subdir="downloads")
