"""Orchestrate a full audit: catalog parsing + reference scan + duplicate detection."""

from collections.abc import Sequence
from pathlib import Path

from asset_guard import catalog, duplicates, references
from asset_guard.catalog import ParsedImageset
from asset_guard.config import ScannerConfig
from asset_guard.models import AssetIssue, AuditReport, DuplicateImage, IssueType
from asset_guard.references import References


class AssetScanner:
    """Audits Xcode `.xcassets` catalogs against project sources."""

    def __init__(self, config: ScannerConfig | None = None) -> None:
        self.config = config or ScannerConfig()

    def scan(self, xcassets_dirs: Path | Sequence[Path], source_dir: Path) -> AuditReport:
        """Run a full audit of one or more catalogs and return an `AuditReport`."""
        catalogs = [xcassets_dirs] if isinstance(xcassets_dirs, Path) else list(xcassets_dirs)
        for xcassets_dir in catalogs:
            if not xcassets_dir.is_dir():
                msg = f"Asset catalog directory not found: {xcassets_dir}"
                raise FileNotFoundError(msg)
        if not source_dir.is_dir():
            msg = f"Source directory not found: {source_dir}"
            raise FileNotFoundError(msg)

        all_issues: list[AssetIssue] = []
        all_existing_files: list[Path] = []
        defined_assets: list[tuple[str, Path]] = []

        for xcassets_dir in catalogs:
            catalog_issues, catalog_files, catalog_assets = self._scan_catalog(xcassets_dir)
            all_issues.extend(catalog_issues)
            all_existing_files.extend(catalog_files)
            defined_assets.extend(catalog_assets)

        refs = self.collect_used_references(source_dir)
        used_count = 0
        ignored: list[str] = []
        unused: list[tuple[str, Path]] = []
        for name, imageset_dir in defined_assets:
            if refs.uses(name):
                used_count += 1
            elif self.config.is_ignored(name):
                ignored.append(name)
            else:
                unused.append((name, imageset_dir))

        all_issues.extend(
            AssetIssue(
                asset_name=name,
                issue_type=IssueType.UNUSED,
                detail="Asset is not referenced in code, Interface Builder, or plist files",
                path=imageset_dir,
            )
            for name, imageset_dir in sorted(unused)
        )

        return AuditReport(
            xcassets_paths=catalogs,
            source_path=source_dir,
            total_assets=len(defined_assets),
            used_assets_count=used_count,
            issues=all_issues,
            duplicate_images=self.find_duplicate_images(all_existing_files),
            ignored_assets=sorted(ignored),
        )

    def _scan_catalog(
        self,
        xcassets_dir: Path,
    ) -> tuple[list[AssetIssue], list[Path], list[tuple[str, Path]]]:
        issues: list[AssetIssue] = []
        existing: list[Path] = []
        defined: dict[str, Path] = {}

        for imageset_dir in catalog.find_imagesets(xcassets_dir):
            asset_name, existing_files, asset_issues = self.parse_imageset(
                imageset_dir,
                xcassets_dir=xcassets_dir,
            )
            existing.extend(existing_files)
            issues.extend(asset_issues)

            if asset_name in defined:
                issues.append(
                    AssetIssue(
                        asset_name=asset_name,
                        issue_type=IssueType.DUPLICATE_ASSET_NAME,
                        detail=f"Duplicate asset name also defined at: {defined[asset_name]}",
                        path=imageset_dir,
                    )
                )
            else:
                defined[asset_name] = imageset_dir

        return issues, existing, list(defined.items())

    def parse_imageset(
        self,
        imageset_dir: Path,
        *,
        xcassets_dir: Path | None = None,
    ) -> ParsedImageset:
        """Parse an `.imageset` directory and validate files, vectors, and scales."""
        return catalog.parse_imageset(imageset_dir, xcassets_dir=xcassets_dir, config=self.config)

    def find_duplicate_images(self, image_files: list[Path]) -> list[DuplicateImage]:
        """Group image files with identical byte content."""
        return duplicates.find_duplicate_images(image_files)

    def collect_used_references(self, source_dir: Path) -> References:
        """Collect everything in the sources that may reference an asset."""
        return references.collect_used_references(source_dir, self.config)


def audit_assets(
    xcassets_dirs: Path | Sequence[Path],
    source_dir: Path,
    *,
    config: ScannerConfig | None = None,
) -> AuditReport:
    """Convenience function to run `AssetScanner` with optional configuration."""
    return AssetScanner(config=config).scan(xcassets_dirs, source_dir)
