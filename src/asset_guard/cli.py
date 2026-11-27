import json
import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console

from asset_guard.catalog import discover_catalogs
from asset_guard.cleaner import (
    WorktreeState,
    apply_removal,
    plan_removal,
    worktree_state,
)
from asset_guard.config import (
    CONFIG_FILENAME,
    CONFIG_KEYS,
    ConfigError,
    ScannerConfig,
    load_scanner_config,
)
from asset_guard.models import AuditReport, FindingCategory, RemovalPlan
from asset_guard.reporting import render_clean_report, render_markdown_summary, render_text_report
from asset_guard.scanner import AssetScanner

EXIT_SUCCESS = 0
EXIT_ISSUES_FOUND = 1
EXIT_USAGE_ERROR = 2
EXIT_RUNTIME_ERROR = 3
EXIT_PERMISSION_DENIED = 77
EXIT_INTERRUPTED = 130

CI_CONSOLE_WIDTH = 160
FAIL_ON_ANY = "any"


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


app = typer.Typer(
    name="asset-guard",
    help=(
        "Find unused, duplicate, and broken image assets in Xcode projects, "
        "and remove unused ones before you build."
    ),
    add_completion=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
)


def _get_version() -> str:
    try:
        return package_version("asset-guard")
    except PackageNotFoundError:
        return "0.0.0+unknown"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"asset-guard {_get_version()}")
        raise typer.Exit(code=EXIT_SUCCESS)


def _should_disable_color(*, no_color_flag: bool, stream_is_tty: bool) -> bool:
    return no_color_flag or bool(os.environ.get("NO_COLOR")) or not stream_is_tty


def _console_width(stream_is_tty: bool) -> int | None:
    """CI logs are not TTYs; use a wide fixed width so long paths are not folded."""
    if stream_is_tty or os.environ.get("COLUMNS"):
        return None
    return CI_CONSOLE_WIDTH


def _make_stdout_console(*, no_color: bool) -> Console:
    is_tty = sys.stdout.isatty()
    return Console(
        no_color=_should_disable_color(no_color_flag=no_color, stream_is_tty=is_tty),
        highlight=False,
        width=_console_width(is_tty),
    )


def _make_stderr_console(*, no_color: bool) -> Console:
    is_tty = sys.stderr.isatty()
    return Console(
        stderr=True,
        no_color=_should_disable_color(no_color_flag=no_color, stream_is_tty=is_tty),
        highlight=False,
        width=_console_width(is_tty),
    )


def _print_error(err_console: Console, message: str, solutions: list[str]) -> None:
    err_console.print(f"[bold red]✗ Error:[/] {message}")
    err_console.print("\nSolutions:")
    for solution in solutions:
        err_console.print(f"  • {solution}")


def _fail(err_console: Console, message: str, solutions: list[str], code: int) -> NoReturn:
    _print_error(err_console, message, solutions)
    raise typer.Exit(code=code)


def _validate_directory(path: Path, label: str, err_console: Console) -> Path:
    if not path.exists():
        _fail(
            err_console,
            f"{label} not found: {path}",
            [
                "Check that the path exists and is spelled correctly.",
                "Pass --xcassets and --source explicitly if using a custom project layout.",
            ],
            EXIT_USAGE_ERROR,
        )
    if not path.is_dir():
        _fail(
            err_console,
            f"{label} is not a directory: {path}",
            [f"Provide a directory path for {label.lower()}."],
            EXIT_USAGE_ERROR,
        )
    return path


def _discover_xcassets(
    project_path: Path, config: ScannerConfig, err_console: Console
) -> list[Path]:
    catalogs = discover_catalogs(project_path, config.excluded_dirs)
    if not catalogs:
        _fail(
            err_console,
            f"No .xcassets catalog found in: {project_path}",
            [
                "Verify you passed the root directory of an Xcode project.",
                "Use --xcassets PATH to specify your asset catalog directory directly.",
            ],
            EXIT_USAGE_ERROR,
        )
    return catalogs


def _parse_fail_on(
    raw: str | None, *, fail_on_issues: bool, err_console: Console
) -> set[FindingCategory]:
    selected: set[FindingCategory] = set(FindingCategory) if fail_on_issues else set()
    for token in (raw or "").split(","):
        name = token.strip().lower()
        if not name:
            continue
        if name == FAIL_ON_ANY:
            selected.update(FindingCategory)
            continue
        try:
            selected.add(FindingCategory(name))
        except ValueError:
            valid = ", ".join([*FindingCategory, FAIL_ON_ANY])
            _fail(
                err_console,
                f"Unknown --fail-on category: {name}",
                [f"Use a comma-separated list of: {valid}."],
                EXIT_USAGE_ERROR,
            )
    return selected


@dataclass(frozen=True)
class _Context:
    catalogs: list[Path]
    source: Path
    config: ScannerConfig
    fail_on: set[FindingCategory]
    err_console: Console


def _prepare(
    *,
    project_path: Path | None,
    xcassets: list[Path] | None,
    source: Path | None,
    config_path: Path | None,
    ignore: list[str] | None,
    fail_on: str | None,
    fail_on_issues: bool,
    no_color: bool,
    verbose: bool,
) -> _Context:
    err_console = _make_stderr_console(no_color=no_color)
    fail_on_set = _parse_fail_on(fail_on, fail_on_issues=fail_on_issues, err_console=err_console)

    base_path = _validate_directory(
        project_path if project_path is not None else Path.cwd(),
        "Project directory",
        err_console,
    )
    if config_path is None and (base_path / CONFIG_FILENAME).is_file():
        config_path = base_path / CONFIG_FILENAME
    try:
        config = load_scanner_config(config_path, extra_ignores=ignore or ())
    except ConfigError as exc:
        _fail(
            err_console,
            str(exc),
            [f"Fix {CONFIG_FILENAME}. Supported keys: {', '.join(sorted(CONFIG_KEYS))}."],
            EXIT_USAGE_ERROR,
        )

    catalogs = (
        [_validate_directory(path, "Asset catalog", err_console) for path in xcassets]
        if xcassets
        else _discover_xcassets(base_path, config, err_console)
    )
    resolved_source = (
        _validate_directory(source, "Source directory", err_console) if source else base_path
    )

    if verbose:
        if config_path is not None:
            err_console.print(f"[blue]• Info:[/] Using config: {config_path}")
        for catalog in catalogs:
            err_console.print(f"[blue]• Info:[/] Scanning catalog: {catalog}")
        err_console.print(f"[blue]• Info:[/] Scanning sources: {resolved_source}")

    return _Context(catalogs, resolved_source, config, fail_on_set, err_console)


def run_audit(xcassets_paths: list[Path], source_path: Path, config: ScannerConfig) -> AuditReport:
    """Execute the asset audit using `AssetScanner`."""
    return AssetScanner(config).scan(xcassets_paths, source_path)


def _scan(ctx: _Context) -> AuditReport:
    try:
        return run_audit(ctx.catalogs, ctx.source, ctx.config)
    except PermissionError as exc:
        _print_error(
            ctx.err_console,
            f"Permission denied while reading project files: {exc.filename or exc}",
            [
                "Check read permissions on your .xcassets and source directories.",
                "Ensure the directory is accessible by the current user.",
            ],
        )
        raise typer.Exit(code=EXIT_PERMISSION_DENIED) from exc
    except OSError as exc:
        _print_error(
            ctx.err_console,
            f"Audit failed due to filesystem error: {exc}",
            ["Verify the project files are readable and try again."],
        )
        raise typer.Exit(code=EXIT_RUNTIME_ERROR) from exc


def _write_outputs(
    payload: dict[str, object],
    summary_markdown: str,
    *,
    report_json: Path | None,
    summary_file: Path | None,
    err_console: Console,
) -> None:
    try:
        if report_json is not None:
            report_json.parent.mkdir(parents=True, exist_ok=True)
            report_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if summary_file is not None:
            with summary_file.open("a", encoding="utf-8") as handle:
                handle.write(summary_markdown)
    except OSError as exc:
        _fail(
            err_console,
            f"Cannot write report file: {exc}",
            ["Check that the output directory exists and is writable."],
            EXIT_RUNTIME_ERROR,
        )


def _exit_for_findings(report: AuditReport, ctx: _Context) -> None:
    counts = report.count_by_category()
    failing = sorted(category for category in ctx.fail_on if counts[category])
    if failing:
        details = ", ".join(f"{category}={counts[category]}" for category in failing)
        ctx.err_console.print(f"[bold red]✗ Failing because of --fail-on:[/] {details}")
        raise typer.Exit(code=EXIT_ISSUES_FOUND)


def _ensure_safe_to_apply(ctx: _Context, *, force: bool) -> None:
    if force:
        return
    state = worktree_state(ctx.catalogs)
    if state is WorktreeState.CLEAN:
        return
    reason = (
        "Asset catalogs have uncommitted git changes"
        if state is WorktreeState.DIRTY
        else "Cannot verify git status of the asset catalogs (git missing or not a repository)"
    )
    _fail(
        ctx.err_console,
        f"{reason}; refusing to delete files.",
        [
            "Commit or stash your changes so deletions can be reverted with git.",
            "In CI, use a fresh checkout, or pass --force if you accept the risk.",
        ],
        EXIT_USAGE_ERROR,
    )


ProjectArg = Annotated[
    Path | None,
    typer.Argument(
        help="Xcode project root directory (defaults to current directory).",
        show_default=False,
    ),
]
XcassetsOpt = Annotated[
    list[Path] | None,
    typer.Option(
        "--xcassets",
        help="Asset catalog to scan (repeatable). Default: every .xcassets in the project.",
        show_default=False,
    ),
]
SourceOpt = Annotated[
    Path | None,
    typer.Option(
        "--source",
        help="Directory with Swift/ObjC/IB/plist sources (defaults to project_path).",
        show_default=False,
    ),
]
ConfigOpt = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help=f"Config file (default: <project_path>/{CONFIG_FILENAME} if present).",
        show_default=False,
    ),
]
IgnoreOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--ignore",
        help="Glob of asset names to never report as unused or remove (repeatable).",
        show_default=False,
    ),
]
FormatOpt = Annotated[
    OutputFormat,
    typer.Option("--format", case_sensitive=False, help="Output format on stdout."),
]
NoColorOpt = Annotated[
    bool,
    typer.Option(
        "--no-color",
        help="Disable ANSI colors (also respects NO_COLOR and non-TTY output).",
    ),
]
FailOnOpt = Annotated[
    str | None,
    typer.Option(
        "--fail-on",
        help="Exit 1 if findings exist in these categories: unused,duplicates,broken,any.",
        show_default=False,
    ),
]
FailOnIssuesOpt = Annotated[
    bool,
    typer.Option("--fail-on-issues", help="Shorthand for --fail-on any."),
]
ReportJsonOpt = Annotated[
    Path | None,
    typer.Option(
        "--report-json",
        help="Also write the JSON report to this file (for CI artifacts).",
        show_default=False,
    ),
]
SummaryFileOpt = Annotated[
    Path | None,
    typer.Option(
        "--summary-file",
        help="Append a Markdown summary to this file (e.g. $GITHUB_STEP_SUMMARY).",
        show_default=False,
    ),
]
VerboseOpt = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Show detailed scanning diagnostics on stderr."),
]


@app.callback()
def _root(
    *,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """Find unused, duplicate, and broken image assets in Xcode projects."""
    _ = version


@app.command(
    epilog=(
        "Examples:\n\n"
        "  asset-guard audit /path/to/MyApp\n\n"
        "  asset-guard audit --xcassets App/Assets.xcassets --source App\n\n"
        "  asset-guard audit . --format json --fail-on unused,duplicates"
    ),
)
def audit(
    project_path: ProjectArg = None,
    *,
    xcassets: XcassetsOpt = None,
    source: SourceOpt = None,
    config: ConfigOpt = None,
    ignore: IgnoreOpt = None,
    output_format: FormatOpt = OutputFormat.TEXT,
    no_color: NoColorOpt = False,
    fail_on: FailOnOpt = None,
    fail_on_issues: FailOnIssuesOpt = False,
    report_json: ReportJsonOpt = None,
    summary_file: SummaryFileOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Report unused, duplicate, and broken image assets. Never modifies files."""
    ctx = _prepare(
        project_path=project_path,
        xcassets=xcassets,
        source=source,
        config_path=config,
        ignore=ignore,
        fail_on=fail_on,
        fail_on_issues=fail_on_issues,
        no_color=no_color,
        verbose=verbose,
    )
    report = _scan(ctx)
    payload = report.to_dict()

    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(payload, indent=2))
    else:
        render_text_report(report, _make_stdout_console(no_color=no_color))

    _write_outputs(
        payload,
        render_markdown_summary(report),
        report_json=report_json,
        summary_file=summary_file,
        err_console=ctx.err_console,
    )
    _exit_for_findings(report, ctx)


@app.command(
    epilog=(
        "Examples:\n\n"
        "  asset-guard clean .                      # dry run: show what would be removed\n\n"
        "  asset-guard clean . --apply              # delete unused image sets\n\n"
        "  asset-guard clean . --apply --fail-on duplicates,broken --report-json report.json"
    ),
)
def clean(
    project_path: ProjectArg = None,
    *,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually delete unused image sets (default: dry run)."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Skip the git safety check (catalogs must otherwise be committed and clean).",
        ),
    ] = False,
    xcassets: XcassetsOpt = None,
    source: SourceOpt = None,
    config: ConfigOpt = None,
    ignore: IgnoreOpt = None,
    output_format: FormatOpt = OutputFormat.TEXT,
    no_color: NoColorOpt = False,
    fail_on: FailOnOpt = None,
    fail_on_issues: FailOnIssuesOpt = False,
    report_json: ReportJsonOpt = None,
    summary_file: SummaryFileOpt = None,
    verbose: VerboseOpt = False,
) -> None:
    """Remove unused image sets (dry run unless --apply). Duplicates are reported, not removed."""
    ctx = _prepare(
        project_path=project_path,
        xcassets=xcassets,
        source=source,
        config_path=config,
        ignore=ignore,
        fail_on=fail_on,
        fail_on_issues=fail_on_issues,
        no_color=no_color,
        verbose=verbose,
    )
    report = _scan(ctx)
    plan = plan_removal(report)
    applied = apply and bool(plan.candidates)

    if applied:
        _ensure_safe_to_apply(ctx, force=force)
        try:
            apply_removal(plan)
        except OSError as exc:
            _fail(
                ctx.err_console,
                f"Failed while deleting image sets: {exc}",
                ["Check write permissions; restore partial deletions with `git checkout`."],
                EXIT_RUNTIME_ERROR,
            )
        report = _scan(ctx)  # findings that remain after removal

    _emit_clean(
        report,
        plan,
        ctx,
        applied=applied,
        output_format=output_format,
        no_color=no_color,
        report_json=report_json,
        summary_file=summary_file,
    )
    _exit_for_findings(report, ctx)


def _emit_clean(
    report: AuditReport,
    plan: RemovalPlan,
    ctx: _Context,
    *,
    applied: bool,
    output_format: OutputFormat,
    no_color: bool,
    report_json: Path | None,
    summary_file: Path | None,
) -> None:
    payload = {**report.to_dict(), "clean": plan.to_dict(applied=applied)}
    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(payload, indent=2))
    else:
        render_clean_report(plan, report, _make_stdout_console(no_color=no_color), applied=applied)
    _write_outputs(
        payload,
        render_markdown_summary(report, plan=plan, applied=applied),
        report_json=report_json,
        summary_file=summary_file,
        err_console=ctx.err_console,
    )


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        _make_stderr_console(no_color=False).print("\n[yellow]⚠ Operation cancelled.[/]")
        sys.exit(EXIT_INTERRUPTED)


if __name__ == "__main__":
    main()
