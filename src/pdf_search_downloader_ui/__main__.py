"""Module entry point for ``python -m pdf_search_downloader_ui``."""

from __future__ import annotations

from .app import run_app


def main() -> int:
    """Run the package entry point and exit with the Qt return code."""

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
