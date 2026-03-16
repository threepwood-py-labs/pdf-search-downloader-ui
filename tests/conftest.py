from __future__ import annotations

import pytest
from threep_commons.paths import configure_qsettings

from pdf_search_downloader_ui.config import ensure_config_exists, get_default_config
from pdf_search_downloader_ui.constants import APP_IDENTITY
from pdf_search_downloader_ui.ui import MainWindow


@pytest.fixture(autouse=True)
def runtime_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    config_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    configure_qsettings(APP_IDENTITY)
    ensure_config_exists()


@pytest.fixture
def window(qtbot: object) -> MainWindow:
    widget = MainWindow(get_default_config())
    add_widget = qtbot.addWidget
    add_widget(widget)
    widget.show()
    yield widget
    widget.close()
