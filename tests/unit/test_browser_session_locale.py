from __future__ import annotations

from pdf_search_downloader_ui.browser_session import (
    _navigator_languages,
    _stealth_init_script,
)


def test_navigator_languages_uses_configured_locale_variants() -> None:
    assert _navigator_languages("it-IT") == ("it-IT", "it", "en-US", "en")
    assert _navigator_languages("it") == ("it", "en-US", "en")


def test_stealth_init_script_embeds_configured_languages() -> None:
    script = _stealth_init_script("it")

    assert 'get: () => ["it", "en-US", "en"]' in script
