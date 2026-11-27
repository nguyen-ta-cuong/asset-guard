from pathlib import Path

from asset_guard import reporting
from asset_guard.models import (
    AssetIssue,
    AuditReport,
    DuplicateImage,
    IssueType,
    RemovalCandidate,
    RemovalPlan,
)
from asset_guard.reporting import display_path, render_markdown_summary


def _report(root: Path) -> AuditReport:
    catalog = root / "Assets.xcassets"
    return AuditReport(
        xcassets_paths=[catalog],
        source_path=root,
        total_assets=3,
        used_assets_count=1,
        issues=[
            AssetIssue("old", IssueType.UNUSED, "unused", catalog / "old.imageset"),
            AssetIssue("bad", IssueType.MISSING_FILE, "a.png missing", catalog / "bad.imageset"),
        ],
        duplicate_images=[
            DuplicateImage(
                "abc", 2048, [catalog / "x.imageset/a.png", catalog / "y.imageset/a.png"]
            )
        ],
        ignored_assets=["promo"],
    )


def test_display_path_relative_and_fallback(tmp_path):
    assert display_path(tmp_path / "A" / "b.png", tmp_path) == str(Path("A/b.png"))
    assert display_path(Path("/elsewhere/x"), tmp_path) == "/elsewhere/x"


def test_markdown_audit_summary(tmp_path):
    markdown = render_markdown_summary(_report(tmp_path))

    assert markdown.startswith("## Asset Guard\n")
    assert "| Unused assets | 1 |" in markdown
    assert "| Ignored assets | 1 |" in markdown
    assert "| Duplicate findings | 1 |" in markdown
    assert "### Unused assets" in markdown
    assert "2.0 KB wasted: `Assets.xcassets/x.imageset/a.png`" in markdown
    assert "`bad` (missing_file): a.png missing" in markdown


def test_markdown_clean_summary_lists_plan_instead_of_unused(tmp_path):
    report = _report(tmp_path)
    plan = RemovalPlan([RemovalCandidate("old", report.issues[0].path, 1024)])

    dry_run = render_markdown_summary(report, plan=plan, applied=False)
    applied = render_markdown_summary(report, plan=plan, applied=True)

    assert "### Would remove (dry run): 1 unused image set(s), 1.0 KB" in dry_run
    assert "### Unused assets" not in dry_run
    assert "### Removed: 1 unused image set(s)" in applied


def test_markdown_lists_are_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(reporting, "MARKDOWN_LIST_LIMIT", 1)
    report = _report(tmp_path)
    report.issues.append(AssetIssue("old2", IssueType.UNUSED, "", tmp_path / "old2.imageset"))

    markdown = render_markdown_summary(report)

    assert "…and 1 more (see the JSON report)" in markdown
