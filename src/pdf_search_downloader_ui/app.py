"""Application bootstrap for the PDF search downloader UI."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication
from threep_commons.logging import setup_logging_from_identity
from threep_commons.paths import configure_qsettings

from .config import ensure_config_exists, load_config, save_config
from .constants import APP_DISPLAY_NAME, APP_IDENTITY
from .runtime_paths import app_data_dir
from .ui import MainWindow, run_setup_wizard


def build_application(argv: list[str] | None = None) -> QApplication:
    """Build or reuse the process ``QApplication`` instance."""

    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_IDENTITY.app_name)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName(APP_IDENTITY.org_name)
    return app


def run_app(argv: list[str] | None = None) -> int:
    """Initialize runtime helpers and start the Qt event loop."""

    configure_qsettings(APP_IDENTITY)
    app_data_dir().mkdir(parents=True, exist_ok=True)
    setup_logging_from_identity(APP_IDENTITY)
    ensure_config_exists()

    app = build_application(argv)
    config = load_config()
    if not config.setup_completed:
        configured = run_setup_wizard(config)
        if configured is None:
            return 1
        save_config(configured)
        config = configured

    window = MainWindow(config)
    if window.has_saved_window_geometry():
        window.show()
    else:
        window.showMaximized()
    return app.exec()
