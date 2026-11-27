import shutil
import subprocess
from pathlib import Path

import pytest

from asset_guard import cleaner
from asset_guard.cleaner import (
    RemovalCandidate,
    RemovalPlan,
    WorktreeState,
    apply_removal,
    plan_removal,
    worktree_state,
)
from asset_guard.models import AssetIssue, AuditReport, IssueType


def _imageset(catalog: Path, name: str, payload: bytes = b"12345") -> Path:
    path = catalog / f"{name}.imageset"
    path.mkdir(parents=True)
    (path / "Contents.json").write_text("{}", encoding="utf-8")
    (path / "a.png").write_bytes(payload)
    return path


def _report(catalog: Path, issues: list[AssetIssue]) -> AuditReport:
    return AuditReport(
        xcassets_paths=[catalog],
        source_path=catalog.parent,
        total_assets=len(issues),
        used_assets_count=0,
        issues=issues,
    )


def test_plan_only_includes_unused_imagesets_inside_catalogs(tmp_path):
    catalog = tmp_path / "Assets.xcassets"
    unused = _imageset(catalog, "unused")
    broken = _imageset(catalog, "broken")
    outside = _imageset(tmp_path / "Elsewhere", "outside")
    report = _report(
        catalog,
        [
            AssetIssue("unused", IssueType.UNUSED, "", unused),
            AssetIssue("broken", IssueType.MISSING_FILE, "", broken),
            AssetIssue("outside", IssueType.UNUSED, "", outside),
            AssetIssue("gone", IssueType.UNUSED, "", catalog / "gone.imageset"),
            AssetIssue("catalog", IssueType.UNUSED, "", catalog / "Contents.json"),
        ],
    )

    plan = plan_removal(report)

    assert [c.asset_name for c in plan.candidates] == ["unused"]
    assert plan.reclaimed_bytes == len("{}") + len(b"12345")
    assert plan.to_dict(applied=False) == {
        "applied": False,
        "count": 1,
        "reclaimed_bytes": 7,
        "imagesets": [{"asset_name": "unused", "path": str(unused), "size_bytes": 7}],
    }


def test_apply_removal_deletes_dirs_and_unlinks_symlinks(tmp_path):
    catalog = tmp_path / "Assets.xcassets"
    real = _imageset(catalog, "real")
    target = _imageset(tmp_path / "Shared", "target")
    link = catalog / "link.imageset"
    link.symlink_to(target, target_is_directory=True)

    apply_removal(
        RemovalPlan(
            [RemovalCandidate("real", real, 1), RemovalCandidate("link", link, 1)],
        )
    )

    assert not real.exists()
    assert not link.exists()
    assert target.exists()  # symlink target is never deleted


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_worktree_state_clean_dirty_and_unknown(tmp_path):
    repo = tmp_path / "repo"
    catalog = repo / "Assets.xcassets"
    _imageset(catalog, "logo")
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)

    assert worktree_state([catalog]) is WorktreeState.CLEAN

    (catalog / "new.txt").write_text("x", encoding="utf-8")
    assert worktree_state([catalog]) is WorktreeState.DIRTY

    not_a_repo = tmp_path / "plain" / "Assets.xcassets"
    not_a_repo.mkdir(parents=True)
    assert worktree_state([not_a_repo]) is WorktreeState.UNKNOWN


def test_worktree_state_unknown_without_git(monkeypatch, tmp_path):
    monkeypatch.setattr(cleaner.shutil, "which", lambda _name: None)

    assert worktree_state([tmp_path]) is WorktreeState.UNKNOWN
    assert worktree_state([]) is WorktreeState.UNKNOWN
