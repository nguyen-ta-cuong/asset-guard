import json
import os
import sys
from enum import StrEnum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from asset_guard.models import AuditReport

EXIT_SUCCESS = 0
EXIT_ISSUES_FOUND = 1
EXIT_USAGE_ERROR = 2
EXIT_PERMISSION_DENIED = 77
EXIT_INTERRUPTED = 130

BYTES_PER_UNIT = 1024
SHORT_SHA_LENGTH = 12


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


app = typer.Typer(
    name="asset-guard",
    help="Audit unused, broken, and duplicate image assets in Xcode projects.",
    add_completion=True,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


def _get_version() -> str:
    try:
        return package_version("asset-guard")
    except PackageNotFoundError:
        return "0.1.0"


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"asset-guard {_get_version()}")
        raise typer.Exit(code=EXIT_SUCCESS)


def _should_disable_color(*, no_color_flag: bool, stream_is_tty: bool) -> bool:
    return no_color_flag or bool(os.environ.get("NO_COLOR")) or not stream_is_tty


def _make_stdout_console(*, no_color: bool) -> Console:
    return Console(
        no_color=_should_disable_color(
            no_color_flag=no_color,
            stream_is_tty=sys.stdout.isatty(),
        ),
        highlight=False,
    )


def _make_stderr_console(*, no_color: bool) -> Console:
    return Console(
        stderr=True,
        no_color=_should_disable_color(
            no_color_flag=no_color,
            stream_is_tty=sys.stderr.isatty(),
        ),
        highlight=False,
    )


def _print_error(err_console: Console, message: str, solutions: list[str]) -> None:
    err_console.print(f"[bold red]✗ Error:[/] {message}")
    err_console.print("\nSolutions:")
    for solution in solutions:
        err_console.print(f"  • {solution}")


def _validate_directory(path: Path, label: str, err_console: Console) -> Path:
    if not path.exists():
        _print_error(
            err_console,
            f"{label} not found: {path}",
            [
                "Check that the path exists and is spelled correctly.",
                "Pass --xcassets and --source explicitly if using a custom project layout.",
            ],
        )
        raise typer.Exit(code=EXIT_USAGE_ERROR)
    if not path.is_dir():
        _print_error(
            err_console,
            f"{label} is not a directory: {path}",
            [f"Provide a directory path for {label.lower()}."],
        )
        raise typer.Exit(code=EXIT_USAGE_ERROR)
    return path


def _discover_xcassets(project_path: Path, err_console: Console) -> Path:
    catalog = next(
        (candidate for candidate in sorted(project_path.rglob("*.xcassets")) if candidate.is_dir()),
        None,
    )
    if catalog is None:
        _print_error(
            err_console,
            f"No .xcassets catalog found in: {project_path}",
            [
                "Verify you passed the root directory of an Xcode project.",
                "Use --xcassets PATH to specify your asset catalog directory directly.",
            ],
        )
        raise typer.Exit(code=EXIT_USAGE_ERROR)
    return catalog


def _resolve_paths(
    project_path: Path | None,
    xcassets: Path | None,
    source: Path | None,
    err_console: Console,
) -> tuple[Path, Path]:
    if xcassets is not None and source is not None:
        resolved_xcassets = _validate_directory(xcassets, "Asset catalog", err_console)
        resolved_source = _validate_directory(source, "Source directory", err_console)
        return resolved_xcassets, resolved_source

    base_path = _validate_directory(
        project_path if project_path is not None else Path.cwd(),
        "Project directory",
        err_console,
    )
    resolved_xcassets = (
        _validate_directory(xcassets, "Asset catalog", err_console)
        if xcassets is not None
        else _discover_xcassets(base_path, err_console)
    )
    resolved_source = (
        _validate_directory(source, "Source directory", err_console)
        if source is not None
        else base_path
    )
    return resolved_xcassets, resolved_source


def _format_bytes(size_bytes: int) -> str:
    if size_bytes < BYTES_PER_UNIT:
        return f"{size_bytes} B"
    size = float(size_bytes)
    for unit in ("KB", "MB", "GB"):
        size /= BYTES_PER_UNIT
        if size < BYTES_PER_UNIT or unit == "GB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} GB"  # pragma: no cover


def run_audit(xassets_path: Path, source_path: Path) -> AuditReport:
    """Execute the asset audit for the given catalog and source directory.

    Delegates to the scanner once `asset_guard.scanner` is implemented.
    """
    return AuditReport(
        xassets_path=xassets_path,
        source_path=source_path,
        total_assets=0,
        used_assets_count=0,
    )


def _render_text_report(report: AuditReport, console: Console) -> None:
    has_findings = bool(report.issues or report.duplicate_images)
    status_line = (
        f"[bold red]✗ Audit found {len(report.issues)} issue(s) and "
        f"{len(report.duplicate_images)} duplicate group(s).[/]"
        if has_findings
        else "[bold green]✓ Audit passed — no asset issues or duplicates found.[/]"
    )

    summary_lines = [
        f"Catalog:       {report.xassets_path}",
        f"Source:        {report.source_path}",
        f"Total assets:  {report.total_assets}",
        f"Used assets:   {report.used_assets_count}",
        f"Unused assets: {len(report.unused_assets)}",
        f"Issues:        {len(report.issues)}",
        f"Duplicates:    {len(report.duplicate_images)}",
        f"Wasted space:  {_format_bytes(report.total_wasted_space_bytes)}",
    ]
    console.print(
        Panel.fit(
            "\n".join(summary_lines),
            title="Asset Guard Audit Summary",
            border_style="red" if has_findings else "green",
        )
    )
    console.print(status_line)

    if report.issues:
        issues_table = Table(title="Asset Issues")
        issues_table.add_column("Asset", style="cyan")
        issues_table.add_column("Type", style="yellow")
        issues_table.add_column("Detail")
        issues_table.add_column("Path", style="dim")
        for issue in report.issues:
            issues_table.add_row(
                issue.asset_name,
                issue.issue_type,
                issue.detail,
                str(issue.path),
            )
        console.print(issues_table)

    if report.duplicate_images:
        duplicates_table = Table(title="Duplicate Images")
        duplicates_table.add_column("SHA-256", style="magenta")
        duplicates_table.add_column("Files", style="cyan")
        duplicates_table.add_column("File Size", justify="right")
        duplicates_table.add_column("Wasted Space", justify="right", style="yellow")
        for duplicate in report.duplicate_images:
            duplicates_table.add_row(
                duplicate.sha256[:SHORT_SHA_LENGTH],
                "\n".join(str(file_path) for file_path in duplicate.files),
                _format_bytes(duplicate.file_size_bytes),
                _format_bytes(duplicate.wasted_space_bytes),
            )
        console.print(duplicates_table)


@app.command(
    epilog=(
        "Examples:\n\n"
        "  asset-guard /path/to/MyXcodeProject\n\n"
        "  asset-guard --xcassets App/Assets.xcassets --source App/Sources\n\n"
        "  asset-guard /path/to/MyXcodeProject --format json --fail-on-issues"
    ),
)
def audit(
    project_path: Annotated[
        Path | None,
        typer.Argument(
            help="Xcode project root directory (defaults to current directory).",
            show_default=False,
        ),
    ] = None,
    *,
    xcassets: Annotated[
        Path | None,
        typer.Option(
            "--xcassets",
            help="Path to .xcassets catalog (overrides auto-discovery).",
            show_default=False,
        ),
    ] = None,
    source: Annotated[
        Path | None,
        typer.Option(
            "--source",
            help="Path to Swift source directory (defaults to project_path).",
            show_default=False,
        ),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            case_sensitive=False,
            help="Output format (text or json).",
        ),
    ] = OutputFormat.TEXT,
    no_color: Annotated[
        bool,
        typer.Option(
            "--no-color",
            help="Disable ANSI colors (also respects NO_COLOR and non-TTY output).",
        ),
    ] = False,
    fail_on_issues: Annotated[
        bool,
        typer.Option(
            "--fail-on-issues",
            help="Exit with code 1 when issues or duplicate images are found.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Show detailed scanning diagnostics on stderr.",
        ),
    ] = False,
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
    """Audit unused, broken, and duplicate image assets in Xcode projects."""
    _ = version
    err_console = _make_stderr_console(no_color=no_color)
    resolved_xcassets, resolved_source = _resolve_paths(
        project_path=project_path,
        xcassets=xcassets,
        source=source,
        err_console=err_console,
    )

    if verbose:
        err_console.print(f"[blue]• Info:[/] Scanning catalog: {resolved_xcassets}")
        err_console.print(f"[blue]• Info:[/] Scanning sources: {resolved_source}")

    try:
        report = run_audit(resolved_xcassets, resolved_source)
    except PermissionError as exc:
        _print_error(
            err_console,
            f"Permission denied while reading project files: {exc.filename or exc}",
            [
                "Check read permissions on your .xcassets and source directories.",
                "Ensure the directory is accessible by the current user.",
            ],
        )
        raise typer.Exit(code=EXIT_PERMISSION_DENIED) from exc
    except OSError as exc:
        _print_error(
            err_console,
            f"Audit failed due to filesystem error: {exc}",
            ["Verify the project files are readable and try again."],
        )
        raise typer.Exit(code=EXIT_ISSUES_FOUND) from exc

    if output_format is OutputFormat.JSON:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        stdout_console = _make_stdout_console(no_color=no_color)
        _render_text_report(report, stdout_console)

    if fail_on_issues and (report.issues or report.duplicate_images):
        raise typer.Exit(code=EXIT_ISSUES_FOUND)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        _make_stderr_console(no_color=False).print("\n[yellow]⚠ Operation cancelled.[/]")
        sys.exit(EXIT_INTERRUPTED)


if __name__ == "__main__":
    main()
