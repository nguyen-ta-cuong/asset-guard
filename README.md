# asset-guard

A lightweight Python (3.12+) CLI tool to audit **unused** and **broken** image assets in Xcode projects (`.xcassets`), built with zero third-party runtime dependencies.

> [!NOTE]
> **Current status (`v0.1.0`):** The repository currently contains the project setup (`pyproject.toml`, `uv.lock`) and the initial CLI entry point (`src/asset_guard/__init__.py`). The `models.py`, `scanner.py`, `cli.py`, and `tests/test_scanner.py` modules below describe the planned implementation.

---

## Features

- **Asset Catalog Scanning (`.xcassets`):** Discovers `.imageset` directories and parses each `Contents.json`.
- **Broken Asset Detection:** Reports image sets whose `Contents.json` references a `filename` missing on disk or contains invalid JSON.
- **Unused Asset Detection:** Scans Swift source files (`.swift`) for image references (e.g. `UIImage(named:)`, SwiftUI `Image(...)`) and flags assets that are never referenced.
- **Zero Runtime Dependencies:** Relies solely on the Python Standard Library (`dataclasses`, `json`, `pathlib`, `re`, `argparse`).

---

## Requirements

- **Python** `>= 3.12` (pinned in `.python-version`)
- **[uv](https://docs.astral.sh/uv/)** for environment, dependency, and build management

---

## Setup

1. Clone the repository and enter the project directory:

   ```bash
   git clone https://github.com/nguyen-ta-cuong/asset-guard.git
   cd asset-guard
   ```

2. Create the virtual environment (`.venv`) and install development dependencies (including `pytest`):

   ```bash
   uv sync
   ```

3. Verify the CLI entry point runs:

   ```bash
   uv run asset-guard
   ```

---

## Usage

### Run the CLI

```bash
uv run asset-guard
```

Planned CLI interface once `cli.py` is implemented with `argparse`:

```bash
# Audit an Xcode project at the specified directory
uv run asset-guard /path/to/MyXcodeProject

# Display help and available options
uv run asset-guard --help
```

### Run Tests

```bash
uv run pytest
```

---

## Project Structure

```text
asset-guard/
├── pyproject.toml              # Project metadata, scripts, build config & dependencies
├── uv.lock                     # Locked dependency versions
├── README.md                   # Setup and usage guide
├── docs/
│   └── swift-to-python.md      # Swift-to-Python learning notes & concept mapping
├── src/
│   └── asset_guard/
│       ├── __init__.py         # Package initialization & CLI entry point (asset_guard:main)
│       ├── models.py           # Data models (dataclass)
│       ├── scanner.py          # Contents.json parsing, Swift scanning & audit logic
│       └── cli.py              # Command-line interface (argparse)
└── tests/
    └── test_scanner.py         # Unit tests (pytest)
```

### Module Overview

| Module | Responsibility |
| --- | --- |
| `asset_guard.models` | Defines typed data structures (`@dataclass`) representing image assets and audit results. |
| `asset_guard.scanner` | Walks `.xcassets` catalogs, parses `Contents.json`, scans `.swift` files, and classifies unused/broken assets. |
| `asset_guard.cli` | Parses CLI arguments (`argparse`), invokes the scanner, and formats audit output. |
| `tests/test_scanner.py` | Unit tests verifying JSON parsing, Swift reference matching, and edge cases using `pytest`. |

---

## Learning Notes

Coming from Swift / iOS development? See [docs/swift-to-python.md](docs/swift-to-python.md) for a side-by-side knowledge map between Swift and Python concepts used in this project.
