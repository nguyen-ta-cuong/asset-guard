import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from typer.testing import CliRunner

from asset_guard import cli, main
from asset_guard.cleaner import WorktreeState
from asset_guard.models import AssetIssue, AuditReport, DuplicateImage, IssueType
from asset_guard.reporting import format_bytes

runner = CliRunner()


@pytest.fixture
def xcode_project(tmp_path: Path) -> tuple[Path, Path, Path]:
    project_root = tmp_path / "MyApp"
    xcassets = project_root / "Resources" / "Assets.xcassets"
    sources = project_root / "Sources"
    xcassets.mkdir(parents=True)
    sources.mkdir(parents=True)
    return project_root, xcassets, sources


def _add_imageset(xcassets: Path, name: str, payload: bytes | None = None) -> Path:
    imageset = xcassets / f"{name}.imageset"
    imageset.mkdir(parents=True)
    (imageset / "logo.svg").write_bytes(payload or f"<svg id='{name}'/>".encode())
    (imageset / "Contents.json").write_text(
        json.dumps({"images": [{"filename": "logo.svg", "idiom": "universal"}]}),
        encoding="utf-8",
    )
    return imageset


def _fake_report(xcassets: Path, project_root: Path, issues=None, duplicates=None) -> AuditReport:
    return AuditReport(
        xcassets_paths=[xcassets],
        source_path=project_root,
        total_assets=4,
        used_assets_count=2,
        issues=issues or [],
        duplicate_images=duplicates or [],
    )


def _stub_scan(monkeypatch, report: AuditReport) -> None:
    monkeypatch.setattr(cli, "run_audit", lambda *_args: report)


# --- app-level -------------------------------------------------------------------


def test_root_help_lists_commands():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "audit" in result.stdout
    assert "clean" in result.stdout
    assert "--version" in result.stdout
    assert "--install-completion" in result.stdout


@pytest.mark.parametrize("command", ["audit", "clean"])
def test_command_help_lists_ci_options(command):
    result = runner.invoke(cli.app, [command, "--help"])

    assert result.exit_code == 0
    for option in [
        "--xcassets",
        "--source",
        "--config",
        "--ignore",
        "--format",
        "--no-color",
        "--fail-on",
        "--report-json",
        "--summary-file",
    ]:
        assert option in result.stdout, option


def test_clean_help_lists_apply_and_force():
    result = runner.invoke(cli.app, ["clean", "--help"])

    assert "--apply" in result.stdout
    assert "--force" in result.stdout


def test_version_flag_outputs_version():
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.startswith("asset-guard ")


def test_get_version_fallback_when_package_not_found(monkeypatch):
    def _raise_not_found(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(cli, "package_version", _raise_not_found)

    assert cli._get_version() == "0.0.0+unknown"  # noqa: SLF001


# --- audit -----------------------------------------------------------------------


def test_clean_audit_text_output(xcode_project):
    project_root, xcassets, _sources = xcode_project

    result = runner.invoke(cli.app, ["audit", str(project_root), "--no-color"])

    assert result.exit_code == 0
    assert "Asset Guard Audit Summary" in result.stdout
    assert xcassets.name in result.stdout
    assert "✓ Audit passed" in result.stdout


def test_audit_defaults_to_current_working_directory(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project
    monkeypatch.chdir(project_root)

    result = runner.invoke(cli.app, ["audit", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["xcassets_paths"] == [str(xcassets)]
    assert payload["source_path"] == str(project_root)


def test_audit_discovers_all_catalogs_but_skips_dependencies(xcode_project):
    project_root, xcassets, _sources = xcode_project
    widget = project_root / "Widget" / "Widget.xcassets"
    widget.mkdir(parents=True)
    (project_root / ".build" / "checkouts" / "Lib" / "Lib.xcassets").mkdir(parents=True)
    (project_root / "Pods" / "SDK" / "SDK.xcassets").mkdir(parents=True)

    result = runner.invoke(cli.app, ["audit", str(project_root), "--format", "json"])

    assert json.loads(result.stdout)["xcassets_paths"] == [str(xcassets), str(widget)]


def test_audit_explicit_catalogs_and_source(xcode_project):
    _project_root, xcassets, sources = xcode_project
    second = xcassets.parent / "Second.xcassets"
    second.mkdir()

    result = runner.invoke(
        cli.app,
        [
            "audit",
            "--xcassets",
            str(xcassets),
            "--xcassets",
            str(second),
            "--source",
            str(sources),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["xcassets_paths"] == [str(xcassets), str(second)]
    assert payload["source_path"] == str(sources)


def test_audit_renders_issues_and_duplicates_in_text_mode(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project
    report = _fake_report(
        xcassets,
        project_root,
        issues=[
            AssetIssue(
                asset_name="hero_banner",
                issue_type=IssueType.UNUSED,
                detail="Not referenced",
                path=xcassets / "hero_banner.imageset",
            )
        ],
        duplicates=[
            DuplicateImage(
                sha256="abcdef1234567890",
                file_size_bytes=2048,
                files=[xcassets / "a.png", xcassets / "b.png"],
            )
        ],
    )
    _stub_scan(monkeypatch, report)

    result = runner.invoke(cli.app, ["audit", str(project_root), "--no-color"])

    assert result.exit_code == 0
    assert "✗ Audit found 1 issue(s) and 1 duplicate group(s)." in result.stdout
    assert "hero_banner" in result.stdout
    assert "abcdef123456" in result.stdout
    assert "2.0 KB" in result.stdout


def test_audit_json_output_and_verbose_stream_separation(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project
    (project_root / ".asset-guard.toml").write_text('ignore = ["x"]\n', encoding="utf-8")
    report = _fake_report(xcassets, project_root)
    _stub_scan(monkeypatch, report)

    result = runner.invoke(cli.app, ["audit", str(project_root), "--format", "json", "-v"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == report.to_dict()
    assert "Using config:" in result.stderr
    assert "Scanning catalog:" in result.stderr
    assert "Scanning sources:" in result.stderr


def test_audit_writes_report_json_and_appends_summary(xcode_project, tmp_path):
    project_root, xcassets, _sources = xcode_project
    _add_imageset(xcassets, "orphan")
    report_json = tmp_path / "out" / "report.json"
    summary = tmp_path / "summary.md"
    summary.write_text("existing\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "audit",
            str(project_root),
            "--report-json",
            str(report_json),
            "--summary-file",
            str(summary),
        ],
    )

    assert result.exit_code == 0
    assert (
        json.loads(report_json.read_text(encoding="utf-8"))["summary"]["unused_assets_count"] == 1
    )
    summary_text = summary.read_text(encoding="utf-8")
    assert summary_text.startswith("existing\n## Asset Guard")
    assert "`orphan`" in summary_text


def test_unwritable_report_file_is_runtime_error(xcode_project):
    project_root, _xcassets, sources = xcode_project

    result = runner.invoke(cli.app, ["audit", str(project_root), "--report-json", str(sources)])

    assert result.exit_code == cli.EXIT_RUNTIME_ERROR
    assert "Cannot write report file" in result.stderr


@pytest.mark.parametrize(
    ("args", "expected_exit"),
    [
        ([], 0),
        (["--fail-on", "broken"], 0),
        (["--fail-on", "duplicates"], 1),
        (["--fail-on", "unused"], 1),
        (["--fail-on", " unused , broken "], 1),
        (["--fail-on", "any"], 1),
        (["--fail-on-issues"], 1),
    ],
)
def test_fail_on_categories(xcode_project, monkeypatch, args, expected_exit):
    project_root, xcassets, _sources = xcode_project
    report = _fake_report(
        xcassets,
        project_root,
        issues=[AssetIssue("orphan", IssueType.UNUSED, "", xcassets / "orphan.imageset")],
        duplicates=[DuplicateImage("d", 1, [xcassets / "a.png", xcassets / "b.png"])],
    )
    _stub_scan(monkeypatch, report)

    result = runner.invoke(cli.app, ["audit", str(project_root), *args])

    assert result.exit_code == expected_exit
    if expected_exit:
        assert "Failing because of --fail-on" in result.stderr


def test_unknown_fail_on_category_is_usage_error(xcode_project):
    project_root, _xcassets, _sources = xcode_project

    result = runner.invoke(cli.app, ["audit", str(project_root), "--fail-on", "nope"])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "Unknown --fail-on category: nope" in result.stderr


def test_invalid_config_is_usage_error(xcode_project):
    project_root, _xcassets, _sources = xcode_project
    (project_root / ".asset-guard.toml").write_text("ignore = [", encoding="utf-8")

    result = runner.invoke(cli.app, ["audit", str(project_root)])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "Invalid TOML" in result.stderr


def test_ignore_option_and_explicit_config(xcode_project, tmp_path):
    project_root, xcassets, _sources = xcode_project
    _add_imageset(xcassets, "promo_banner")
    _add_imageset(xcassets, "legacy")
    config = tmp_path / "custom.toml"
    config.write_text('ignore = ["legacy"]\n', encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "audit",
            str(project_root),
            "--config",
            str(config),
            "--ignore",
            "promo_*",
            "--format",
            "json",
            "--fail-on",
            "unused",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["summary"]["ignored_assets_count"] == 2


def test_missing_project_path_exits_with_usage_error(tmp_path):
    result = runner.invoke(cli.app, ["audit", str(tmp_path / "does-not-exist")])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "Project directory not found" in result.stderr
    assert "Solutions:" in result.stderr


def test_file_instead_of_directory_exits_with_usage_error(tmp_path):
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("hello", encoding="utf-8")

    result = runner.invoke(cli.app, ["audit", str(file_path)])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "is not a directory" in result.stderr


def test_missing_xcassets_catalog_exits_with_usage_error(tmp_path):
    empty_project = tmp_path / "EmptyProject"
    empty_project.mkdir()

    result = runner.invoke(cli.app, ["audit", str(empty_project)])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert "No .xcassets catalog found" in result.stderr
    assert "--xcassets" in result.stderr


def test_permission_error_returns_posix_code_77(xcode_project, monkeypatch):
    project_root, _xcassets, _sources = xcode_project

    def _deny(*_args: object) -> AuditReport:
        raise PermissionError(13, "Permission denied", str(project_root))

    monkeypatch.setattr(cli, "run_audit", _deny)

    result = runner.invoke(cli.app, ["audit", str(project_root)])

    assert result.exit_code == cli.EXIT_PERMISSION_DENIED
    assert "Permission denied" in result.stderr


def test_os_error_returns_runtime_error_code(xcode_project, monkeypatch):
    project_root, _xcassets, _sources = xcode_project

    def _fail(*_args: object) -> AuditReport:
        msg = "I/O failure"
        raise OSError(msg)

    monkeypatch.setattr(cli, "run_audit", _fail)

    result = runner.invoke(cli.app, ["audit", str(project_root)])

    assert result.exit_code == cli.EXIT_RUNTIME_ERROR
    assert "filesystem error" in result.stderr


# --- clean -----------------------------------------------------------------------


@pytest.fixture
def project_with_unused(xcode_project):
    project_root, xcassets, sources = xcode_project
    used = _add_imageset(xcassets, "app_logo")
    unused = _add_imageset(xcassets, "legacy_icon")
    (sources / "AppView.swift").write_text("let img = Image(.appLogo)\n", encoding="utf-8")
    return project_root, used, unused


def test_clean_dry_run_does_not_delete(project_with_unused):
    project_root, used, unused = project_with_unused

    result = runner.invoke(cli.app, ["clean", str(project_root), "--no-color"])

    assert result.exit_code == 0
    assert "legacy_icon" in result.stdout
    assert "Dry run: would remove 1 unused image set(s)" in result.stdout
    assert unused.exists()
    assert used.exists()


def test_clean_apply_deletes_unused_and_reports_remaining(project_with_unused, monkeypatch):
    project_root, used, unused = project_with_unused
    monkeypatch.setattr(cli, "worktree_state", lambda _paths: WorktreeState.CLEAN)

    result = runner.invoke(
        cli.app, ["clean", str(project_root), "--apply", "--format", "json", "--fail-on", "unused"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["clean"]["applied"] is True
    assert payload["clean"]["count"] == 1
    assert payload["clean"]["imagesets"][0]["asset_name"] == "legacy_icon"
    assert payload["summary"]["unused_assets_count"] == 0  # report is re-scanned after removal
    assert not unused.exists()
    assert used.exists()


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (WorktreeState.DIRTY, "uncommitted git changes"),
        (WorktreeState.UNKNOWN, "Cannot verify git status"),
    ],
)
def test_clean_apply_refuses_without_clean_git(project_with_unused, monkeypatch, state, message):
    project_root, _used, unused = project_with_unused
    monkeypatch.setattr(cli, "worktree_state", lambda _paths: state)

    result = runner.invoke(cli.app, ["clean", str(project_root), "--apply"])

    assert result.exit_code == cli.EXIT_USAGE_ERROR
    assert message in result.stderr
    assert unused.exists()


def test_clean_apply_force_skips_git_check(project_with_unused, monkeypatch):
    project_root, _used, unused = project_with_unused

    def _must_not_be_called(_paths: list[Path]) -> WorktreeState:
        raise AssertionError

    monkeypatch.setattr(cli, "worktree_state", _must_not_be_called)

    result = runner.invoke(
        cli.app, ["clean", str(project_root), "--apply", "--force", "--no-color"]
    )

    assert result.exit_code == 0
    assert "✓ Removed 1 unused image set(s)" in result.stdout
    assert not unused.exists()


def test_clean_with_nothing_to_remove_skips_git_check(xcode_project, monkeypatch):
    project_root, _xcassets, _sources = xcode_project
    monkeypatch.setattr(cli, "worktree_state", lambda _paths: WorktreeState.DIRTY)

    result = runner.invoke(cli.app, ["clean", str(project_root), "--apply", "--no-color"])

    assert result.exit_code == 0
    assert "No unused image sets to remove" in result.stdout


def test_clean_apply_delete_failure_is_runtime_error(project_with_unused, monkeypatch):
    project_root, _used, _unused = project_with_unused

    def _boom(_plan: object) -> None:
        msg = "read-only file system"
        raise OSError(msg)

    monkeypatch.setattr(cli, "apply_removal", _boom)

    result = runner.invoke(cli.app, ["clean", str(project_root), "--apply", "--force"])

    assert result.exit_code == cli.EXIT_RUNTIME_ERROR
    assert "Failed while deleting image sets" in result.stderr


def test_clean_reports_remaining_duplicates_and_broken(xcode_project, monkeypatch):
    project_root, xcassets, _sources = xcode_project
    report = _fake_report(
        xcassets,
        project_root,
        issues=[AssetIssue("bad", IssueType.MISSING_FILE, "", xcassets / "bad.imageset")],
        duplicates=[DuplicateImage("d", 10, [xcassets / "a.png", xcassets / "b.png"])],
    )
    _stub_scan(monkeypatch, report)

    result = runner.invoke(
        cli.app, ["clean", str(project_root), "--no-color", "--fail-on", "duplicates"]
    )

    assert result.exit_code == cli.EXIT_ISSUES_FOUND
    assert "1 duplicate finding(s) remain" in result.stdout
    assert "1 broken image set issue(s)" in result.stdout


def test_clean_summary_file_for_github(project_with_unused, tmp_path):
    project_root, _used, _unused = project_with_unused
    summary = tmp_path / "step_summary.md"

    result = runner.invoke(cli.app, ["clean", str(project_root), "--summary-file", str(summary)])

    assert result.exit_code == 0
    assert "### Would remove (dry run): 1 unused image set(s)" in summary.read_text(
        encoding="utf-8"
    )


# --- helpers & entry point -------------------------------------------------------


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
    assert format_bytes(size_bytes) == expected


def test_should_disable_color_respects_flags_env_and_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert not cli._should_disable_color(no_color_flag=False, stream_is_tty=True)  # noqa: SLF001
    assert cli._should_disable_color(no_color_flag=True, stream_is_tty=True)  # noqa: SLF001
    assert cli._should_disable_color(no_color_flag=False, stream_is_tty=False)  # noqa: SLF001

    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._should_disable_color(no_color_flag=False, stream_is_tty=True)  # noqa: SLF001


def test_console_width_is_wide_in_ci(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    assert cli._console_width(stream_is_tty=False) == cli.CI_CONSOLE_WIDTH  # noqa: SLF001
    assert cli._console_width(stream_is_tty=True) is None  # noqa: SLF001

    monkeypatch.setenv("COLUMNS", "100")
    assert cli._console_width(stream_is_tty=False) is None  # noqa: SLF001


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
    _add_imageset(xcassets, "app_logo")
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
        ["audit", str(project_root), "--format", "json", "--fail-on-issues"],
    )

    assert result.exit_code == cli.EXIT_ISSUES_FOUND
    payload = json.loads(result.stdout)
    assert payload["summary"]["total_assets"] == 2
    assert payload["summary"]["used_assets_count"] == 1
    issue_types = {issue["issue_type"] for issue in payload["issues"]}
    assert issue_types == {"incomplete_scales", "unused"}
