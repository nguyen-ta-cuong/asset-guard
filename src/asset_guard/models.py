from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class FindingCategory(StrEnum):
    """Groups of findings that CI can gate on (`--fail-on`)."""

    UNUSED = "unused"
    DUPLICATES = "duplicates"
    BROKEN = "broken"


class IssueType(StrEnum):
    INVALID_JSON = "invalid_json"
    EMPTY_IMAGESET = "empty_imageset"
    MISSING_FILE = "missing_file"
    INVALID_VECTOR = "invalid_vector"
    INCOMPLETE_SCALES = "incomplete_scales"
    DUPLICATE_ASSET_NAME = "duplicate_asset_name"
    UNUSED = "unused"

    @property
    def category(self) -> FindingCategory:
        if self is IssueType.UNUSED:
            return FindingCategory.UNUSED
        if self is IssueType.DUPLICATE_ASSET_NAME:
            return FindingCategory.DUPLICATES
        return FindingCategory.BROKEN


@dataclass(frozen=True)
class AssetIssue:
    asset_name: str
    issue_type: IssueType
    detail: str
    path: Path


@dataclass(frozen=True)
class DuplicateImage:
    sha256: str
    file_size_bytes: int
    files: list[Path]

    @property
    def wasted_space_bytes(self) -> int:
        return self.file_size_bytes * max(0, len(self.files) - 1)


@dataclass
class AuditReport:
    xcassets_paths: list[Path]
    source_path: Path
    total_assets: int
    used_assets_count: int
    issues: list[AssetIssue] = field(default_factory=list)
    duplicate_images: list[DuplicateImage] = field(default_factory=list)
    ignored_assets: list[str] = field(default_factory=list)

    @property
    def unused_assets(self) -> list[str]:
        return [issue.asset_name for issue in self.issues if issue.issue_type == IssueType.UNUSED]

    @property
    def total_wasted_space_bytes(self) -> int:
        return sum(dup.wasted_space_bytes for dup in self.duplicate_images)

    def count_by_category(self) -> dict[FindingCategory, int]:
        """Count findings per CI gate category (duplicate images count as duplicates)."""
        counts = dict.fromkeys(FindingCategory, 0)
        for issue in self.issues:
            counts[IssueType(issue.issue_type).category] += 1
        counts[FindingCategory.DUPLICATES] += len(self.duplicate_images)
        return counts

    def to_dict(self) -> dict[str, object]:
        return {
            # `xcassets_path` (first catalog) is kept for pre-0.2 consumers.
            "xcassets_path": str(self.xcassets_paths[0]) if self.xcassets_paths else "",
            "xcassets_paths": [str(path) for path in self.xcassets_paths],
            "source_path": str(self.source_path),
            "total_assets": self.total_assets,
            "summary": {
                "total_assets": self.total_assets,
                "used_assets_count": self.used_assets_count,
                "unused_assets_count": len(self.unused_assets),
                "ignored_assets_count": len(self.ignored_assets),
                "total_issues": len(self.issues),
                "total_duplicate_images": len(self.duplicate_images),
                "total_wasted_space_bytes": self.total_wasted_space_bytes,
            },
            "issues": [
                {
                    "asset_name": issue.asset_name,
                    "issue_type": str(issue.issue_type),
                    "detail": issue.detail,
                    "path": str(issue.path),
                }
                for issue in self.issues
            ],
            "duplicate_images": [
                {
                    "sha256": dup.sha256,
                    "file_size_bytes": dup.file_size_bytes,
                    "files": [str(file) for file in dup.files],
                    "wasted_space_bytes": dup.wasted_space_bytes,
                }
                for dup in self.duplicate_images
            ],
        }


@dataclass(frozen=True)
class RemovalCandidate:
    """An unused `.imageset` directory that `clean` will delete."""

    asset_name: str
    path: Path
    size_bytes: int


@dataclass(frozen=True)
class RemovalPlan:
    """Everything `clean` will delete, in deterministic order."""

    candidates: list[RemovalCandidate]

    @property
    def reclaimed_bytes(self) -> int:
        return sum(candidate.size_bytes for candidate in self.candidates)

    def to_dict(self, *, applied: bool) -> dict[str, object]:
        return {
            "applied": applied,
            "count": len(self.candidates),
            "reclaimed_bytes": self.reclaimed_bytes,
            "imagesets": [
                {
                    "asset_name": candidate.asset_name,
                    "path": str(candidate.path),
                    "size_bytes": candidate.size_bytes,
                }
                for candidate in self.candidates
            ],
        }
