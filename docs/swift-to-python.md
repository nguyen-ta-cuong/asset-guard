# Swift to Python Knowledge Map (`asset-guard`)

A living reference for mapping Swift / iOS ecosystem concepts to Python (3.12+) while building `asset-guard`.

---

## Quick Ecosystem Mapping

| Category | Swift / iOS | Python (`asset-guard`) |
| --- | --- | --- |
| Package manifest | `Package.swift` | `pyproject.toml` |
| Dependency lockfile | `Package.resolved` | `uv.lock` |
| Toolchain / isolation | Xcode / Swift toolchain | `.python-version` + `.venv` (managed by `uv`) |
| Resolve / sync deps | `swift package resolve` | `uv sync` |
| Run CLI target | `swift run asset-guard` | `uv run asset-guard` |
| Run tests | `swift test` (`XCTest` / `Swift Testing`) | `uv run pytest` (`pytest`) |
| Data models | `struct` + `Equatable` / `Hashable` | `@dataclass(frozen=True)` |
| JSON decoding | `Codable` + `JSONDecoder` | `json.load()` / `json.loads()` → `dict` → dataclass |
| File paths & I/O | `URL` + `FileManager` | `pathlib.Path` |
| Regex matching | `Regex` / `NSRegularExpression` | `re` |
| CLI argument parsing | `swift-argument-parser` | `argparse` (built into stdlib) |
| Optionals | `String?` (`nil`) | `str \| None` (`None`) |

---

## 5 Core Differences Between Python and Swift

| # | Area | Swift | Python (3.12+) |
| --- | --- | --- | --- |
| 1 | **Type System** | Static typing (compile-time checks), mandatory `Optional` unwrapping | Dynamic typing at runtime + **Type Hints** for IDEs/linters |
| 2 | **Data Models & JSON** | `struct` (value type) + built-in `Codable` | `@dataclass` (reference type) + `dict` parsing via `json` |
| 3 | **Packaging & Runtime** | SwiftPM (`Package.swift`, `Package.resolved`), compiled native binary | `uv` (`pyproject.toml`, `uv.lock`), interpreted inside `.venv` |
| 4 | **Error & Resource Handling** | `throws` / `do-try-catch`, `guard`, `defer` (favors LBYL) | `try / except` (unchecked exceptions, favors EAFP) & `with` |
| 5 | **Syntax & Modules** | Braces `{}` / `camelCase` / `private`, target-wide visibility | 4-space indentation / `snake_case` / `_` convention, explicit `import`s |

### 1. Static Typing (Swift) vs. Dynamic Typing + Type Hints (Python)

In **Swift**, the compiler enforces types at build time and requires explicit unwrapping of optionals (`String?`) with `if let` or `guard let`.

In **Python**, types are resolved dynamically at runtime. Modern Python (3.10+/3.12) supports **Type Hints** (such as `str | None`) so editors and static checkers (`pyright`, `mypy`) catch type mismatches early—similar to Swift—even though the Python interpreter does not enforce them at runtime.

```swift
// Swift
func findImage(named name: String) -> String? {
    return name.isEmpty ? nil : name
}
```

```python
# Python 3.12+
def find_image(name: str) -> str | None:
    return None if not name else name
```

### 2. `struct` / `Codable` (Swift) vs. `@dataclass` / `json` (Python)

In **Swift**, models are typically `struct`s (**value types** copied on assignment) and conform to `Codable` for automatic `Contents.json` decoding.

In **Python**, every object is a **reference type** on the heap. In `models.py`, `@dataclass` generates boilerplate (`__init__`, `__repr__`, `__eq__`) automatically, and `@dataclass(frozen=True)` adds immutability similar to `let` properties in a Swift `struct`. Because the standard library has no `Codable` equivalent, JSON is decoded into a `dict` via `json.load()` and mapped into dataclasses explicitly.

```swift
// Swift
struct ImageAsset: Codable, Equatable {
    let name: String
    let path: String
    let isBroken: Bool
}
```

```python
# Python (src/asset_guard/models.py)
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ImageAsset:
    name: str
    path: Path
    is_broken: bool = False
```

### 3. SwiftPM vs. `uv` and Virtual Environments (`.venv`)

In **Swift**, `Package.swift` defines targets and dependencies, `Package.resolved` pins versions, and `swift build` compiles a native executable.

In **Python**, because code runs in an interpreter, each project uses an isolated **Virtual Environment (`.venv`)** to keep its Python version and installed packages separate from the system environment. In `asset-guard`, `uv` manages `.venv`, dependency locking (`uv.lock`), script execution (`uv run`), and packaging (`uv_build`).

### 4. `throws` / `do-try-catch` (Swift) vs. `try / except` (EAFP) & `with` (Python)

In **Swift**, throwing functions must declare `throws` in their signature and call sites must use `try`. Idiomatic Swift often leans toward **LBYL** (*Look Before You Leap*, e.g. `guard FileManager.default.fileExists(...)`).

In **Python**, exceptions are unchecked—functions do not declare what they may raise. Idiomatic Python favors **EAFP** (*Easier to Ask for Forgiveness than Permission*): attempt the operation (like opening and parsing `Contents.json` in `scanner.py`) and catch specific exceptions in `except`. The `with` statement (**context manager**) guarantees the file is closed automatically even when an error occurs (similar to pairing `open` with `defer { close() }` in Swift).

```swift
// Swift
func loadContents(at url: URL) throws -> Data {
    return try Data(contentsOf: url)
}
```

```python
# Python (src/asset_guard/scanner.py)
import json
from pathlib import Path

def load_contents(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```

### 5. Block Syntax, Naming Conventions, Modules & Access Control

- **Code blocks:** Swift uses curly braces `{ }`; Python uses **indentation (4 spaces)** to define scope.
- **Naming (PEP 8):** Swift uses `camelCase` for variables/functions and `PascalCase` for types/files (`AssetScanner.swift`). Python uses `snake_case` for variables, functions, files, and packages (`asset_guard`, `scanner.py`, `scan_assets()`), reserving `PascalCase` for classes (`ImageAsset`).
- **Modules & imports:** In Swift, all `.swift` files within the same target can see each other's `internal` symbols automatically. In Python, each `.py` file is a separate **module** (and a directory with `__init__.py` is a **package**), so symbols must be imported explicitly (e.g. `from asset_guard.models import ImageAsset`).
- **Access control:** Python has no compiler-enforced `private` or `fileprivate` keywords; instead, a leading underscore (`_parse_contents_json`) signals by convention that a helper is internal.

---

## Module-by-Module Learning Roadmap

Use this section to track Python concepts learned as each module in `asset-guard` is built:

| File | Swift Parallel | Python Concepts Practiced |
| --- | --- | --- |
| `pyproject.toml` & `uv.lock` | `Package.swift` & `Package.resolved` | PEP 621 metadata, `[project.scripts]`, `[dependency-groups]`, `uv` workflow |
| `src/asset_guard/models.py` | `struct` models, `Equatable`, `enum` | `@dataclass(frozen=True)`, type hints (`str \| None`, `list[...]`, `set[...]`), `Path` |
| `src/asset_guard/scanner.py` | `FileManager`, `JSONDecoder`, `Regex` | `Path.rglob()`, `with open(...)`, `json.load()`, `re.findall()`, EAFP `try/except`, set operations |
| `src/asset_guard/cli.py` | `ArgumentParser`, `exit(...)` | `argparse.ArgumentParser`, exit codes (`sys.exit`), separating CLI from core logic |
| `tests/test_scanner.py` | `XCTest` / `Swift Testing` | `pytest` test functions, `tmp_path` fixture, `assert` statements, parametrized tests |
