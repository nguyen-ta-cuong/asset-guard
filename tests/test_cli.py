import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asset_guard import cli, main
from asset_guard.models import AssetIssue, AuditReport, DuplicateImage

runner = CliRunner()


@pytest.fixture
def xcode_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "MyApp"
    xcassets = project_root / "Resources" / "Assets.xcassets"
    sources = project_root / "Sources"
    xcassets.mkdir(parents=True)
    sources.mkdir(parents=True)
    return project_root, xcassets, sources


def test_help_lists_options_and_examples():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "--xcassets" in result.stdout
    assert "--source" in result.stdout
    assert "--format" in result.stdout
    assert "--no-color" in result.stdout
    assert "--fail-on-issues" in result.stdout
    assert "--version" in result.stdout
    assert "--install-completion" in result.stdout


def test_version_flag_outputs_version():
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("asset-guard ")


def test_get_version_fallback_when_package_not_found(monkeypatch):
    def _raise_not_found(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(cli, "package_version", _raise_not_found)

    assert cli._get_version() == "0.1.0"  # noqa: SLF001


def test_clean_audit_text_output(xcode_project):
    project_root, xcassets, _sources = xcode_project

    result = runner.invoke(cli.app, [str(project_root), "--no-color"])

    assert result.exit_code == 0
    assert "Asset Guard Audit Summary" in result.stdout
    assert xcassets.name in result.stdout
    assert "✓ Audit passed" in result.stdout


def test_audit_defaults_to_current_working_directory(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project
    monkeypatch.chdir(project_root)

    result = runner.invoke(cli.app, ["--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["xcassets_path"] == str(xcassets)
    assert payload["source_path"] == str(project_root)


def test_audit_explicit_xcassets_and_source_without_project_path(xcode_project):
    _project_root, xcassets, sources = xcode_project

    result = runner.invoke(
        cli.app,
        ["--xcassets", str(xcassets), "--source", str(sources), "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["xcassets_path"] == str(xcassets)
    assert payload["source_path"] == str(sources)


def test_audit_renders_issues_and_duplicates_in_text_mode(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project
    report = AuditReport(
        xcassets_path=xcassets,
        source_path=project_root,
        total_assets=4,
        used_assets_count=2,
        issues=[
            AssetIssue(
                asset_name="hero_banner",
                issue_type="unused",
                detail="Not referenced in Swift files",
                path=xcassets / "hero_banner.imageset",
            )
        ],
        duplicate_images=[
            DuplicateImage(
                sha256="abcdef1234567890",
                file_size_bytes=2048,
                files=[xcassets / "a.png", xcassets / "b.png"],
            )
        ],
    )
    monkeypatch.setattr(cli, "run_audit", lambda _x, _s: report)

    result = runner.invoke(cli.app, [str(project_root), "--no-color"])

    assert result.exit_code == 0
    assert "✗ Audit found 1 issue(s) and 1 duplicate group(s)." in result.stdout
    assert "hero_banner" in result.stdout
    assert "abcdef123456" in result.stdout
    assert "2.0 KB" in result.stdout


def test_audit_json_output_and_verbose_stream_separation(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project
    report = AuditReport(
        xcassets_path=xcassets,
        source_path=project_root,
        total_assets=3,
        used_assets_count=2,
        issues=[
            AssetIssue(
                asset_name="orphan",
                issue_type="unused",
                detail="Unused asset",
                path=xcassets / "orphan.imageset",
            )
        ],
    )
    monkeypatch.setattr(cli, "run_audit", lambda _x, _s: report)

    result = runner.invoke(cli.app, [str(project_root), "--format", "json", "--verbose"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == report.to_dict()
    assert "Scanning catalog:" in result.stderr
    assert "Scanning sources:" in result.stderr


def test_fail_on_issues_exit_codes(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project

    clean_result = runner.invoke(cli.app, [str(project_root), "--fail-on-issues"])
    assert clean_result.exit_code == 0

    report_with_issue = AuditReport(
        xcassets_path=xcassets,
        source_path=project_root,
        total_assets=1,
        used_assets_count=0,
        issues=[
            AssetIssue(
                asset_name="broken",
                issue_type="missing_file",
                detail="missing.png not found",
                path=xcassets / "broken.imageset",
            )
        ],
    )
    monkeypatch.setattr(cli, "run_audit", lambda _x, _s: report_with_issue)

    failing_result = runner.invoke(cli.app, [str(project_root), "--fail-on-issues"])
    assert failing_result.exit_code == cli.EXIT_ISSUES_FOUND


def test_missing_project_path_exits_with_usage_error(tmp_path):
    missing = tmp_path / "does-not-exist"

    result = runner.invoke(cli.app, [str(missing)])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "Project directory not found" in result.stderr
    assert "Solutions:" in result.stderr


def test_file_instead_of_directory_exits_with_usage_error(tmp_path):
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("hello", encoding="utf-8")

    result = runner.invoke(cli.app, [str(file_path)])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "is not a directory" in result.stderr


def test_missing_xcassets_catalog_exits_with_usage_error(tmp_path):
    empty_project = tmp_path / "EmptyProject"
    empty_project.mkdir()

    result = runner.invoke(cli.app, [str(empty_project)])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "No .xcassets catalog found" in result.stderr
    assert "--xcassets" in result.stderr


def test_permission_error_returns_posix_code_77(xcode_project, monkeypatch):
    project_root, _xcassets, _sources = xcode_project

    def _deny(_x: Path, _s: Path) -> AuditReport:
        raise PermissionError(13, "Permission denied", str(project_root))

    monkeypatch.setattr(cli, "run_audit", _deny)

    result = runner.invoke(cli.app, [str(project_root)])

    assert result.exit_code == cli.EXIT_PERMISSION_DENIED
    assert "Permission denied" in result.stderr


def test_os_error_returns_general_error_code(xcode_project, monkeypatch):
    project_root, _xcassets, _sources = xcode_project

    def _fail(_x: Path, _s: Path) -> AuditReport:
        msg = "I/O failure"
        raise OSError(msg)

    monkeypatch.setattr(cli, "run_audit", _fail)

    result = runner.invoke(cli.app, [str(project_root)])

    assert result.exit_code == cli.EXIT_ISSUES_FOUND
    assert "filesystem error" in result.stderr


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1024 * 1024, "1.0 MB"),
        (2 * 1024 * 1024 * 1024, "2.0 GB"),
    ],
)
def test_format_bytes(size_bytes, expected):
    assert cli._format_bytes(size_bytes) == expected  # noqa: SLF001


def test_should_disable_color_respects_flags_env_and_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert not cli._should_disable_color(no_color_flag=False, stream_is_tty=True)  # noqa: SLF001
    assert cli._should_disable_color(no_color_flag=True, stream_is_tty=True)  # noqa: SLF001
    assert cli._should_disable_color(no_color_flag=False, stream_is_tty=False)  # noqa: SLF001

    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._should_disable_color(no_color_flag=False, stream_is_tty=True)  # noqa: SLF001


def test_main_handles_keyboard_interrupt(monkeypatch):
    def _interrupt() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "app", _interrupt)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == cli.EXIT_INTERRUPTED


def test_main_delegates_to_app(monkeypatch):
    called = False

    def _fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "app", _fake_app)

    main()

    assert called


def test_cli_end_to_end_with_real_scanner(xcode_project):
    project_root, xcassets, sources = xcode_project

    used_set = xcassets / "app_logo.imageset"
    used_set.mkdir()
    (used_set / "logo.svg").write_text("<svg></svg>", encoding="utf-8")
    (used_set / "Contents.json").write_text(
        json.dumps({"images": [{"filename": "logo.svg", "idiom": "universal"}]}),
        encoding="utf-8",
    )

    unused_set = xcassets / "legacy_icon.imageset"
    unused_set.mkdir()
    (unused_set / "icon@2x.png").write_bytes(b"png-bytes-2x")
    (unused_set / "Contents.json").write_text(
        json.dumps({"images": [{"filename": "icon@2x.png", "idiom": "universal", "scale": "2x"}]}),
        encoding="utf-8",
    )

    (sources / "AppView.swift").write_text(
        "import SwiftUI\nlet img = Image(.appLogo)\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli.app,
        [str(project_root), "--format", "json", "--fail-on-issues"],
    )

    assert result.exit_code == cli.EXIT_ISSUES_FOUND
    payload = json.loads(result.stdout)
    assert payload["summary"]["total_assets"] == 2
    assert payload["summary"]["used_assets_count"] == 1
    issue_types = {issue["issue_type"] for issue in payload["issues"]}
    assert issue_types == {"incomplete_scales", "unused"}
