"""Plan and apply removal of unused image sets.

Planning is pure: it reads an `AuditReport` and never touches the filesystem except
to measure sizes. All deletion happens in `apply_removal`. Only whole `.imageset`
directories inside an audited catalog are ever removed, so `Contents.json` files stay
valid and nothing outside the catalogs can be deleted.

Duplicate images are *not* removed: a duplicate whose name is still referenced in
code would disappear at runtime. They are reported so CI can fail on them instead.
"""

import shutil
import subprocess
from enum import StrEnum
from pathlib import Path

from asset_guard.models import AuditReport, IssueType, RemovalCandidate, RemovalPlan


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _is_inside_any(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(
        resolved.is_relative_to(root.resolve()) and resolved != root.resolve() for root in roots
    )


def plan_removal(report: AuditReport) -> RemovalPlan:
    """List unused `.imageset` directories that are safe to delete."""
    candidates = [
        RemovalCandidate(
            asset_name=issue.asset_name,
            path=issue.path,
            size_bytes=_dir_size(issue.path),
        )
        for issue in report.issues
        if issue.issue_type == IssueType.UNUSED
        and issue.path.suffix == ".imageset"
        and issue.path.is_dir()
        and _is_inside_any(issue.path, report.xcassets_paths)
    ]
    return RemovalPlan(candidates=sorted(candidates, key=lambda c: (c.asset_name, c.path)))


def apply_removal(plan: RemovalPlan) -> None:
    """Delete every planned `.imageset` directory."""
    for candidate in plan.candidates:
        if candidate.path.is_symlink():
            candidate.path.unlink()
        else:
            shutil.rmtree(candidate.path)


class WorktreeState(StrEnum):
    CLEAN = "clean"
    DIRTY = "dirty"
    UNKNOWN = "unknown"  # git missing, not a repo, or paths outside the repo


def worktree_state(paths: list[Path]) -> WorktreeState:
    """Report whether the given paths have uncommitted git changes."""
    git = shutil.which("git")
    if git is None or not paths:
        return WorktreeState.UNKNOWN
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, paths are local dirs
        [
            git,
            "-C",
            str(paths[0].parent),
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *(str(path.resolve()) for path in paths),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return WorktreeState.UNKNOWN
    return WorktreeState.DIRTY if result.stdout.strip() else WorktreeState.CLEAN
