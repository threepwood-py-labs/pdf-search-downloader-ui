# pdf-search-downloader-ui

Windows desktop application for browser-driven PDF search and download workflows.

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)

- [Usage](#usage)
- [Configuration](#configuration)
- [Logging](#logging)

- [Project Structure](#project-structure)
- [Architecture Patterns](#architecture-patterns)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Legal Disclaimer](#legal-disclaimer)

## Features

- Headed Playwright-powered Google and Bing scraping with a persistent browser profile
- Forced `filetype:pdf` search flow with configurable language, market, page, and result limits
- One-hop landing-page PDF resolution for results that are not already direct PDF links
- Bulk auto-download workflow with SQLite manifest-backed duplicate skipping
- Shared `threep-commons` runtime foundation for QSettings, logging, data paths, and widget identity
- First-run setup wizard for providers, download directory, browser profile, and duplicate policy

## Requirements

- **Python 3.13+**

- **Windows** (10 or later)


## Installation

```bat
python scripts\windows\setup_env.py
```

Synchronizes the local `.venv` by running `uv sync --locked` on each launch of the setup script and falls back to `uv sync` when lock sync is unavailable (for example, first bootstrap before `uv.lock` exists).

Manual development alternative:

```bat
uv sync
```

The setup script also installs the Playwright Chromium runtime used by the browser workflow.


## Usage

### Recommended

```bat
pyw scripts\windows\run_app_gui.pyw
```

### Direct

```bat
python -m pdf_search_downloader_ui
```

### Workflow

1. Enter a query such as `bilancio comunale 2025`.
2. Start the run to open a visible browser session and scrape Google/Bing results.
3. If a consent page or captcha appears, solve it in the browser and press **Resume browser**.
4. The app resolves direct PDFs or a single landing-page hop, then downloads results into the configured folder.

## Configuration

Application identity is defined in `src/pdf_search_downloader_ui/constants.py` and passed directly to `threep_commons`.

- configure QSettings with `threep_commons.paths.configure_qsettings(APP_IDENTITY, config_dir_override=...)`
- resolve runtime storage with `threep_commons.paths.resolve_app_data_dir(APP_IDENTITY, override_dir=...)`
- use `CONFIG_DIR` and `DATA_DIR` for environment overrides
- runtime config keys include:
  - `config/providers/google_enabled`
  - `config/providers/bing_enabled`
  - `config/search/default_language`
  - `config/search/default_market`
  - `config/search/max_pages`
  - `config/search/max_results`
  - `config/downloads/output_dir`
  - `config/downloads/skip_duplicates`
  - `config/browser/profile_dir`
  - `prefs/setup_completed`
  - `prefs/last_query`

## Logging

Initialize runtime logging directly through `threep_commons.logging`.

```python
from pdf_search_downloader_ui.constants import APP_IDENTITY
from threep_commons.logging import setup_logging_from_identity

setup_logging_from_identity(APP_IDENTITY)
```


## Project Structure

```text
pdf-search-downloader-ui/
|-- pyproject.toml
|-- uv.lock
|-- src/
|   `-- pdf_search_downloader_ui/
|       |-- __init__.py
|       |-- __main__.py
|       |-- constants.py
|       |-- app.py
|       |-- config.py
|       |-- browser_session.py
|       |-- html_tools.py
|       |-- models.py
|       |-- runtime_paths.py
|       |-- widget_naming.py
|       |-- persistence/
|       |-- providers/
|       |-- services/
|       |-- ui/
|       |-- workers/
|       `-- py.typed
|-- scripts/
|   |-- policy/
|   |   `-- check_standard.py
|   `-- windows/
|       |-- run_app.py
|       |-- run_app_gui.pyw
|       |-- run_tests.py
|       `-- setup_env.py
|-- tests/
|   |-- __init__.py
|   |-- conftest.py
|   |-- unit/
|   |-- gui/
|   `-- integration/
`-- .pre-commit-config.yaml
```

## Architecture Patterns

- Keep top-level window/dialog classes thin and delegate feature logic to focused collaborators.
- Split large concerns into separate modules (actions/layout/operations/persistence/status) while preserving public imports.
- Split GUI tests by feature domain instead of building one large end-to-end test file.
- See `docs/architecture/qt_composition_playbook.md` for the reusable Qt decomposition workflow.

## Development

```bat
hatch run test
hatch run test-cov
hatch run lint:check
hatch run lint:fmt
hatch run lint:types
hatch run lint:policy
hatch run lint:all
hatch build
```

## Troubleshooting

- If the app opens a browser and waits, look at the visible browser window for a consent page or captcha, solve it, then press **Resume browser**.
- If Playwright cannot launch Chromium, rerun `python scripts\windows\setup_env.py` to install the browser runtime.
- If the app keeps skipping results you want, inspect the manifest database under the app data directory and adjust the output folder or duplicate policy.
- If settings look stale, point `CONFIG_DIR` and `DATA_DIR` to a temporary location and relaunch for a clean profile.

---

<!-- legal-disclaimer:start -->
## Legal Disclaimer

THIS SOFTWARE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS, IMPLIED, STATUTORY, OR OTHERWISE, INCLUDING, WITHOUT LIMITATION, ANY IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, NON-INFRINGEMENT, ACCURACY, OR QUIET ENJOYMENT. TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, THE AUTHORS, CONTRIBUTORS, MAINTAINERS, DISTRIBUTORS, AND AFFILIATED PARTIES SHALL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, OR FOR ANY LOSS OF DATA, PROFITS, GOODWILL, BUSINESS OPPORTUNITY, OR SERVICE INTERRUPTION, ARISING OUT OF OR RELATING TO THE USE OF, OR INABILITY TO USE, THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. THIS SOFTWARE HAS BEEN DEVELOPED, IN WHOLE OR IN PART, BY "INTELLIGENT TOOLS"; ACCORDINGLY, OUTPUTS MAY CONTAIN ERRORS OR OMISSIONS, AND YOU ASSUME FULL RESPONSIBILITY FOR INDEPENDENT VALIDATION, TESTING, LEGAL COMPLIANCE, AND SAFE OPERATION PRIOR TO ANY RELIANCE OR DEPLOYMENT.
<!-- legal-disclaimer:end -->
