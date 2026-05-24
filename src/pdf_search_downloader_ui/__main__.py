"""Module entry point for ``python -m pdf_search_downloader_ui``."""

from __future__ import annotations

import sys

from .app import run_app


def main() -> int:
    """Run the package entry point and exit with the Qt return code."""

    if len(sys.argv) > 1 and sys.argv[1] == "auto":
        from .auto_mode import main as run_auto

        return run_auto(sys.argv[2:])
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
