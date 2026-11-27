"""Scanner configuration and `.asset-guard.toml` loading."""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

CONFIG_FILENAME = ".asset-guard.toml"

MODERN_IOS_SCALES: frozenset[str] = frozenset({"2x", "3x"})
VECTOR_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".svg"})
DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {".git", ".build", ".swiftpm", "DerivedData", "Pods", "Carthage"}
)


class ConfigError(ValueError):
    """Raised when `.asset-guard.toml` is unreadable or has invalid values."""


@dataclass(frozen=True)
class ScannerConfig:
    """Configuration controlling Xcode asset catalog scanning rules."""

    expected_scales: frozenset[str] = MODERN_IOS_SCALES
    vector_extensions: frozenset[str] = VECTOR_EXTENSIONS
    excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS
    ignore_patterns: tuple[str, ...] = ()
    scan_interface_builder: bool = True

    def is_ignored(self, asset_name: str) -> bool:
        """Return True when the asset matches an ignore glob (never unused, never removed)."""
        return any(fnmatchcase(asset_name, pattern) for pattern in self.ignore_patterns)


def _string_list(data: dict[str, object], key: str, source: Path) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        msg = f"{source}: '{key}' must be a list of strings"
        raise ConfigError(msg)
    return tuple(str(item) for item in value)


CONFIG_KEYS = frozenset({"ignore", "exclude-dirs", "expected-scales", "scan-interface-builder"})


def load_scanner_config(
    config_path: Path | None,
    *,
    extra_ignores: Iterable[str] = (),
) -> ScannerConfig:
    """Build a `ScannerConfig` from an optional TOML file plus CLI `--ignore` globs.

    Supported keys: `ignore`, `exclude-dirs` (added to the defaults), `expected-scales`,
    `scan-interface-builder`. Unknown keys are rejected so typos do not silently pass.
    """
    data: dict[str, object] = {}
    if config_path is not None:
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            msg = f"Cannot read config file {config_path}: {exc.strerror or exc}"
            raise ConfigError(msg) from exc
        except tomllib.TOMLDecodeError as exc:
            msg = f"Invalid TOML in {config_path}: {exc}"
            raise ConfigError(msg) from exc

        unknown = sorted(set(data) - CONFIG_KEYS)
        if unknown:
            msg = f"{config_path}: unknown key(s): {', '.join(unknown)}"
            raise ConfigError(msg)

    source = config_path or Path(CONFIG_FILENAME)
    ignore = (*_string_list(data, "ignore", source), *extra_ignores)
    excluded = DEFAULT_EXCLUDED_DIRS | frozenset(_string_list(data, "exclude-dirs", source))
    scales = _string_list(data, "expected-scales", source) or tuple(MODERN_IOS_SCALES)

    scan_ib = data.get("scan-interface-builder", True)
    if not isinstance(scan_ib, bool):
        msg = f"{source}: 'scan-interface-builder' must be true or false"
        raise ConfigError(msg)

    return ScannerConfig(
        expected_scales=frozenset(scales),
        excluded_dirs=excluded,
        ignore_patterns=tuple(dict.fromkeys(ignore)),
        scan_interface_builder=scan_ib,
    )
