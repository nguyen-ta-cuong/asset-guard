from pathlib import Path

import pytest

from asset_guard.config import (
    DEFAULT_EXCLUDED_DIRS,
    ConfigError,
    ScannerConfig,
    load_scanner_config,
)


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".asset-guard.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_without_file():
    config = load_scanner_config(None)

    assert config == ScannerConfig()


def test_loads_all_keys_and_merges_cli_ignores(tmp_path):
    path = _write_config(
        tmp_path,
        """
        ignore = ["AppIcon*", "onboarding_*"]
        exclude-dirs = ["Generated"]
        expected-scales = ["3x"]
        scan-interface-builder = false
        """,
    )

    config = load_scanner_config(path, extra_ignores=["promo_*", "AppIcon*"])

    assert config.ignore_patterns == ("AppIcon*", "onboarding_*", "promo_*")
    assert config.excluded_dirs == DEFAULT_EXCLUDED_DIRS | {"Generated"}
    assert config.expected_scales == frozenset({"3x"})
    assert config.scan_interface_builder is False
    assert config.is_ignored("onboarding_2")
    assert not config.is_ignored("logo")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("ignore = [", "Invalid TOML"),
        ('ignore = "logo"', "'ignore' must be a list of strings"),
        ("exclude-dirs = [1]", "'exclude-dirs' must be a list of strings"),
        ('scan-interface-builder = "yes"', "must be true or false"),
        ('ignroe = ["typo"]', "unknown key"),
    ],
)
def test_invalid_config_raises(tmp_path, content, message):
    path = _write_config(tmp_path, content)

    with pytest.raises(ConfigError, match=message):
        load_scanner_config(path)


def test_unreadable_config_raises(tmp_path):
    with pytest.raises(ConfigError, match="Cannot read config file"):
        load_scanner_config(tmp_path / "missing.toml")
