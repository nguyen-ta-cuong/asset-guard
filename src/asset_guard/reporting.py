"""Human-readable renderings: Rich text for terminals/CI logs, Markdown for CI summaries."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from asset_guard.cleaner import RemovalPlan
from asset_guard.models import AuditReport, FindingCategory

BYTES_PER_UNIT = 1024
SHORT_SHA_LENGTH = 12
MARKDOWN_LIST_LIMIT = 50


def display_path(path: Path, base: Path) -> str:
    """Show paths relative to the scanned project when possible (shorter CI logs)."""
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def format_bytes(size_bytes: int) -> str:
    if size_bytes < BYTES_PER_UNIT:
        return f"{size_bytes} B"
    size = float(size_bytes)
    for unit in ("KB", "MB", "GB"):
        size /= BYTES_PER_UNIT
        if size < BYTES_PER_UNIT or unit == "GB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} GB"  # pragma: no cover


def render_text_report(report: AuditReport, console: Console) -> None:
    has_findings = bool(report.issues or report.duplicate_images)
    status_line = (
        f"[bold red]✗ Audit found {len(report.issues)} issue(s) and "
        f"{len(report.duplicate_images)} duplicate group(s).[/]"
        if has_findings
        else "[bold green]✓ Audit passed — no asset issues or duplicates found.[/]"
    )

    summary_lines = [
        *(f"Catalog:       {path}" for path in report.xcassets_paths),
        f"Source:        {report.source_path}",
        f"Total assets:  {report.total_assets}",
        f"Used assets:   {report.used_assets_count}",
        f"Unused assets: {len(report.unused_assets)}",
        f"Ignored:       {len(report.ignored_assets)}",
        f"Issues:        {len(report.issues)}",
        f"Duplicates:    {len(report.duplicate_images)}",
        f"Wasted space:  {format_bytes(report.total_wasted_space_bytes)}",
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
                display_path(issue.path, report.source_path),
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
                "\n".join(
                    display_path(file_path, report.source_path) for file_path in duplicate.files
                ),
                format_bytes(duplicate.file_size_bytes),
                format_bytes(duplicate.wasted_space_bytes),
            )
        console.print(duplicates_table)


def render_clean_report(
    plan: RemovalPlan,
    report: AuditReport,
    console: Console,
    *,
    applied: bool,
) -> None:
    """Print the removal plan (dry run) or what was removed, plus what remains."""
    if plan.candidates:
        table = Table(title="Removed image sets" if applied else "Unused image sets to remove")
        table.add_column("Asset", style="cyan")
        table.add_column("Size", justify="right")
        table.add_column("Path", style="dim")
        for candidate in plan.candidates:
            table.add_row(
                candidate.asset_name,
                format_bytes(candidate.size_bytes),
                display_path(candidate.path, report.source_path),
            )
        console.print(table)

    count = len(plan.candidates)
    size = format_bytes(plan.reclaimed_bytes)
    if not plan.candidates:
        console.print("[bold green]✓ No unused image sets to remove.[/]")
    elif applied:
        console.print(f"[bold green]✓ Removed {count} unused image set(s), reclaimed {size}.[/]")
    else:
        console.print(
            f"[bold yellow]• Dry run:[/] would remove {count} unused image set(s) ({size}). "
            "Re-run with --apply to delete them."
        )

    counts = report.count_by_category()
    if counts[FindingCategory.DUPLICATES]:
        console.print(
            f"[yellow]⚠ {counts[FindingCategory.DUPLICATES]} duplicate finding(s) remain "
            f"({format_bytes(report.total_wasted_space_bytes)} wasted). Duplicates are never "
            "auto-removed; run `asset-guard audit` for details.[/]"
        )
    if counts[FindingCategory.BROKEN]:
        console.print(
            f"[yellow]⚠ {counts[FindingCategory.BROKEN]} broken image set issue(s). "
            "Run `asset-guard audit` for details.[/]"
        )


def _md_list(lines: list[str]) -> list[str]:
    shown = [f"- {line}" for line in lines[:MARKDOWN_LIST_LIMIT]]
    if len(lines) > MARKDOWN_LIST_LIMIT:
        shown.append(f"- …and {len(lines) - MARKDOWN_LIST_LIMIT} more (see the JSON report)")
    return shown


def render_markdown_summary(
    report: AuditReport,
    *,
    plan: RemovalPlan | None = None,
    applied: bool = False,
) -> str:
    """Render a compact Markdown summary (e.g. for `$GITHUB_STEP_SUMMARY`)."""
    counts = report.count_by_category()
    lines = [
        "## Asset Guard",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Catalogs | {len(report.xcassets_paths)} |",
        f"| Total assets | {report.total_assets} |",
        f"| Used assets | {report.used_assets_count} |",
        f"| Unused assets | {counts[FindingCategory.UNUSED]} |",
        f"| Ignored assets | {len(report.ignored_assets)} |",
        f"| Duplicate findings | {counts[FindingCategory.DUPLICATES]} |",
        f"| Wasted space (duplicates) | {format_bytes(report.total_wasted_space_bytes)} |",
        f"| Broken image set issues | {counts[FindingCategory.BROKEN]} |",
    ]

    if plan is not None and plan.candidates:
        verb = "Removed" if applied else "Would remove (dry run)"
        lines += [
            "",
            (
                f"### {verb}: {len(plan.candidates)} unused image set(s), "
                f"{format_bytes(plan.reclaimed_bytes)}"
            ),
            "",
            *_md_list(
                [
                    f"`{c.asset_name}` — `{display_path(c.path, report.source_path)}`"
                    for c in plan.candidates
                ]
            ),
        ]

    unused = [i for i in report.issues if i.issue_type == "unused"]
    if unused and plan is None:
        lines += ["", "### Unused assets", "", *_md_list([f"`{i.asset_name}`" for i in unused])]

    if report.duplicate_images:
        lines += [
            "",
            "### Duplicate images",
            "",
            *_md_list(
                [
                    f"{format_bytes(d.wasted_space_bytes)} wasted: "
                    + ", ".join(f"`{display_path(path, report.source_path)}`" for path in d.files)
                    for d in report.duplicate_images
                ]
            ),
        ]

    other = [i for i in report.issues if i.issue_type != "unused"]
    if other:
        lines += [
            "",
            "### Other issues",
            "",
            *_md_list([f"`{i.asset_name}` ({i.issue_type}): {i.detail}" for i in other]),
        ]

    return "\n".join(lines) + "\n"
