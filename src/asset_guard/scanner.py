import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from asset_guard.models import AssetIssue, AuditReport, DuplicateImage, IssueType

MODERN_IOS_SCALES: frozenset[str] = frozenset({"2x", "3x"})
VECTOR_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".svg"})
DEFAULT_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {".git", ".build", ".swiftpm", "DerivedData", "Pods", "Carthage"}
)

SWIFT_ASSET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'\bUIImage\s*\(\s*named:\s*"([^"]+)"'),
    re.compile(r'\bImage\s*\(\s*"([^"]+)"'),
    re.compile(r'\bImage\s*\(\s*decorative:\s*"([^"]+)"'),
    re.compile(r'#imageLiteral\s*\(\s*resourceName:\s*"([^"]+)"'),
)

SWIFT_SYMBOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bImage\s*\(\s*\.([A-Za-z_][A-Za-z0-9_]*)\s*(?:,|\))"),
    re.compile(r"\bUIImage\s*\(\s*resource:\s*\.([A-Za-z_][A-Za-z0-9_]*)\s*(?:,|\))"),
)

INTERFACE_BUILDER_EXTENSIONS: frozenset[str] = frozenset({".storyboard", ".xib"})
INTERFACE_BUILDER_IMAGE_PATTERN: re.Pattern[str] = re.compile(r'\bimage\s*=\s*"([^"]+)"')

_SWIFT_COMMENT_OR_STRING = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
_SYMBOL_SPLIT_PATTERN = re.compile(r"[^A-Za-z0-9]+")


def compute_sha256(file_path: Path) -> str:
    """Compute the SHA-256 digest of a file using streaming I/O."""
    with file_path.open("rb") as file_handle:
        return hashlib.file_digest(file_handle, "sha256").hexdigest()


def _strip_swift_comments(source: str) -> str:
    """Remove // and /* */ comments while preserving string literals."""

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if token.startswith('"') else " "

    return _SWIFT_COMMENT_OR_STRING.sub(_replace, source)


def _to_swift_symbol(asset_name: str) -> str:
    """Convert an asset catalog name into its Xcode 15+ lowerCamelCase Swift symbol."""
    parts = [part for part in _SYMBOL_SPLIT_PATTERN.split(asset_name) if part]
    if not parts:
        return ""
    first = parts[0]
    first_normalized = first[0].lower() + first[1:]
    rest = [part[0].upper() + part[1:] for part in parts[1:]]
    return "".join([first_normalized, *rest])


def _appearance_label(entry: dict[str, object]) -> str:
    """Return a stable label describing an entry's appearance variant (empty for default)."""
    appearances = entry.get("appearances")
    if not isinstance(appearances, list) or not appearances:
        return ""
    values: list[str] = []
    for item in appearances:
        if isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, str) and value:
                values.append(value)
    return "/".join(sorted(values))


def _load_json_dict(contents_file: Path) -> dict[str, object] | None:
    try:
        parsed: object = json.loads(contents_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_asset_name(imageset_dir: Path, xcassets_dir: Path | None) -> str:
    """Resolve an imageset's asset name, honoring Xcode `provides-namespace` folders."""
    name = imageset_dir.stem
    if xcassets_dir is None:
        return name

    try:
        relative_parents = imageset_dir.relative_to(xcassets_dir).parts[:-1]
    except ValueError:
        return name

    namespaces: list[str] = []
    current = xcassets_dir
    for folder in relative_parents:
        current /= folder
        folder_contents = current / "Contents.json"
        if not folder_contents.is_file():
            continue
        data = _load_json_dict(folder_contents)
        if data is None:
            continue
        properties = data.get("properties")
        if isinstance(properties, dict) and properties.get("provides-namespace") is True:
            namespaces.append(folder)

    return "/".join([*namespaces, name]) if namespaces else name


@dataclass(frozen=True)
class ScannerConfig:
    """Configuration controlling Xcode asset catalog scanning rules."""

    expected_scales: frozenset[str] = MODERN_IOS_SCALES
    vector_extensions: frozenset[str] = VECTOR_EXTENSIONS
    excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS
    swift_patterns: tuple[re.Pattern[str], ...] = SWIFT_ASSET_PATTERNS
    swift_symbol_patterns: tuple[re.Pattern[str], ...] = SWIFT_SYMBOL_PATTERNS
    scan_interface_builder: bool = True


class AssetScanner:
    """Audits Xcode `.xcassets` catalogs against Swift and Interface Builder sources."""

    def __init__(self, config: ScannerConfig | None = None) -> None:
        self.config = config or ScannerConfig()

    def scan(self, xcassets_dir: Path, source_dir: Path) -> AuditReport:
        """Run a full asset audit and return a structured `AuditReport`."""
        if not xcassets_dir.is_dir():
            msg = f"Asset catalog directory not found: {xcassets_dir}"
            raise FileNotFoundError(msg)
        if not source_dir.is_dir():
            msg = f"Source directory not found: {source_dir}"
            raise FileNotFoundError(msg)

        imageset_dirs = sorted(
            candidate for candidate in xcassets_dir.rglob("*.imageset") if candidate.is_dir()
        )
        all_issues: list[AssetIssue] = []
        all_existing_files: list[Path] = []
        defined_assets: dict[str, Path] = {}

        for imageset_dir in imageset_dirs:
            asset_name, existing_files, asset_issues = self.parse_imageset(
                imageset_dir,
                xcassets_dir=xcassets_dir,
            )
            all_existing_files.extend(existing_files)
            all_issues.extend(asset_issues)

            if asset_name in defined_assets:
                all_issues.append(
                    AssetIssue(
                        asset_name=asset_name,
                        issue_type=IssueType.DUPLICATE_ASSET_NAME,
                        detail=(
                            f"Duplicate asset name also defined at: {defined_assets[asset_name]}"
                        ),
                        path=imageset_dir,
                    )
                )
            else:
                defined_assets[asset_name] = imageset_dir

        used_names, used_symbols = self.collect_used_references(source_dir)
        used_defined_assets = {
            name
            for name in defined_assets
            if name in used_names or _to_swift_symbol(name) in used_symbols
        }

        unused_names = sorted(set(defined_assets) - used_defined_assets)
        all_issues.extend(
            AssetIssue(
                asset_name=name,
                issue_type=IssueType.UNUSED,
                detail="Asset is not referenced in Swift or Interface Builder sources",
                path=defined_assets[name],
            )
            for name in unused_names
        )

        duplicate_images = self.find_duplicate_images(all_existing_files)

        return AuditReport(
            xcassets_path=xcassets_dir,
            source_path=source_dir,
            total_assets=len(defined_assets),
            used_assets_count=len(used_defined_assets),
            issues=all_issues,
            duplicate_images=duplicate_images,
        )

    def parse_imageset(
        self,
        imageset_dir: Path,
        *,
        xcassets_dir: Path | None = None,
    ) -> tuple[str, list[Path], list[AssetIssue]]:
        """Parse an `.imageset` directory and validate files, vectors, and scales."""
        asset_name = _resolve_asset_name(imageset_dir, xcassets_dir)
        contents_file = imageset_dir / "Contents.json"

        raw_images, load_issue = self._load_imageset_entries(
            asset_name=asset_name,
            imageset_dir=imageset_dir,
            contents_file=contents_file,
        )
        if load_issue is not None:
            return asset_name, [], [load_issue]

        declared_entries = [
            entry
            for entry in raw_images
            if isinstance(entry.get("filename"), str) and str(entry["filename"]).strip()
        ]
        if not declared_entries:
            return (
                asset_name,
                [],
                [
                    AssetIssue(
                        asset_name=asset_name,
                        issue_type=IssueType.EMPTY_IMAGESET,
                        detail="Image set does not declare any image files in Contents.json",
                        path=contents_file,
                    )
                ],
            )

        existing_files, file_issues = self._check_declared_files(
            asset_name=asset_name,
            imageset_dir=imageset_dir,
            declared_entries=declared_entries,
        )
        scale_issues = self._check_vectors_and_scales(
            asset_name=asset_name,
            contents_file=contents_file,
            declared_entries=declared_entries,
        )
        return asset_name, existing_files, [*file_issues, *scale_issues]

    def _load_imageset_entries(
        self,
        *,
        asset_name: str,
        imageset_dir: Path,
        contents_file: Path,
    ) -> tuple[list[dict[str, object]], AssetIssue | None]:
        if not contents_file.is_file():
            return [], AssetIssue(
                asset_name=asset_name,
                issue_type=IssueType.INVALID_JSON,
                detail="Missing Contents.json",
                path=imageset_dir,
            )

        try:
            raw_data: object = json.loads(contents_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [], AssetIssue(
                asset_name=asset_name,
                issue_type=IssueType.INVALID_JSON,
                detail=f"Invalid JSON syntax: {exc.msg}",
                path=contents_file,
            )

        if not isinstance(raw_data, dict):
            return [], AssetIssue(
                asset_name=asset_name,
                issue_type=IssueType.INVALID_JSON,
                detail="Contents.json root must be a JSON object",
                path=contents_file,
            )

        images_obj = raw_data.get("images", [])
        if not isinstance(images_obj, list) or not all(
            isinstance(item, dict) for item in images_obj
        ):
            return [], AssetIssue(
                asset_name=asset_name,
                issue_type=IssueType.INVALID_JSON,
                detail="Contents.json 'images' field must be a list of objects",
                path=contents_file,
            )

        entries: list[dict[str, object]] = [item for item in images_obj if isinstance(item, dict)]
        return entries, None

    def _check_declared_files(
        self,
        *,
        asset_name: str,
        imageset_dir: Path,
        declared_entries: list[dict[str, object]],
    ) -> tuple[list[Path], list[AssetIssue]]:
        existing_by_resolved: dict[Path, Path] = {}
        missing_filenames: list[str] = []

        for entry in declared_entries:
            filename = str(entry["filename"])
            file_path = imageset_dir / filename
            if file_path.is_file():
                existing_by_resolved.setdefault(file_path.resolve(), file_path)
            elif filename not in missing_filenames:
                missing_filenames.append(filename)

        issues: list[AssetIssue] = []
        if missing_filenames:
            issues.append(
                AssetIssue(
                    asset_name=asset_name,
                    issue_type=IssueType.MISSING_FILE,
                    detail=f"Image file(s) missing on disk: {', '.join(missing_filenames)}",
                    path=imageset_dir,
                )
            )
        return list(existing_by_resolved.values()), issues

    def _check_vectors_and_scales(
        self,
        *,
        asset_name: str,
        contents_file: Path,
        declared_entries: list[dict[str, object]],
    ) -> list[AssetIssue]:
        vector_entries = [
            entry
            for entry in declared_entries
            if Path(str(entry["filename"])).suffix.lower() in self.config.vector_extensions
        ]

        if vector_entries:
            scaled_vectors = [
                str(entry["filename"]) for entry in vector_entries if "scale" in entry
            ]
            if scaled_vectors:
                return [
                    AssetIssue(
                        asset_name=asset_name,
                        issue_type=IssueType.INVALID_VECTOR,
                        detail=(
                            "Vector image should use Single Scale instead of explicit scale: "
                            f"{', '.join(scaled_vectors)}"
                        ),
                        path=contents_file,
                    )
                ]
            return []

        scales_by_appearance: dict[str, set[str]] = defaultdict(set)
        for entry in declared_entries:
            appearance = _appearance_label(entry)
            scale = entry.get("scale")
            if isinstance(scale, str):
                scales_by_appearance[appearance].add(scale)
            else:
                scales_by_appearance.setdefault(appearance, set())

        issues: list[AssetIssue] = []
        for appearance in sorted(scales_by_appearance):
            missing_scales = sorted(self.config.expected_scales - scales_by_appearance[appearance])
            if not missing_scales:
                continue
            detail = (
                f"Missing required scales for {appearance} appearance: {', '.join(missing_scales)}"
                if appearance
                else f"Missing required scales: {', '.join(missing_scales)}"
            )
            issues.append(
                AssetIssue(
                    asset_name=asset_name,
                    issue_type=IssueType.INCOMPLETE_SCALES,
                    detail=detail,
                    path=contents_file,
                )
            )
        return issues

    def find_duplicate_images(self, image_files: list[Path]) -> list[DuplicateImage]:
        """Group image files with identical byte content using size pre-filtering + SHA-256."""
        unique_files: dict[Path, Path] = {}
        for file_path in image_files:
            if file_path.is_file():
                unique_files.setdefault(file_path.resolve(), file_path)

        size_to_paths: dict[int, list[Path]] = defaultdict(list)
        for file_path in unique_files.values():
            size_to_paths[file_path.stat().st_size].append(file_path)

        duplicate_groups: list[DuplicateImage] = []
        for file_size_bytes, candidate_paths in size_to_paths.items():
            if len(candidate_paths) < 2:  # noqa: PLR2004
                continue

            hash_to_paths: dict[str, list[Path]] = defaultdict(list)
            for file_path in candidate_paths:
                hash_to_paths[compute_sha256(file_path)].append(file_path)

            duplicate_groups.extend(
                DuplicateImage(
                    sha256=digest,
                    file_size_bytes=file_size_bytes,
                    files=sorted(paths),
                )
                for digest, paths in hash_to_paths.items()
                if len(paths) > 1
            )

        return sorted(
            duplicate_groups,
            key=lambda group: (-group.wasted_space_bytes, group.sha256),
        )

    def collect_used_assets(self, source_dir: Path) -> set[str]:
        """Collect string-literal asset names referenced in Swift and Interface Builder files."""
        used_names, _used_symbols = self.collect_used_references(source_dir)
        return used_names

    def collect_used_references(self, source_dir: Path) -> tuple[set[str], set[str]]:
        """Collect string asset names and Xcode 15+ Swift symbol references."""
        used_names: set[str] = set()
        used_symbols: set[str] = set()

        for file_path in self._iter_source_files(source_dir):
            suffix = file_path.suffix.lower()
            content = file_path.read_text(encoding="utf-8", errors="ignore")

            if suffix == ".swift":
                stripped = _strip_swift_comments(content)
                for pattern in self.config.swift_patterns:
                    used_names.update(match.group(1) for match in pattern.finditer(stripped))
                for pattern in self.config.swift_symbol_patterns:
                    used_symbols.update(match.group(1) for match in pattern.finditer(stripped))
            elif self.config.scan_interface_builder and suffix in INTERFACE_BUILDER_EXTENSIONS:
                used_names.update(
                    match.group(1) for match in INTERFACE_BUILDER_IMAGE_PATTERN.finditer(content)
                )

        return used_names, used_symbols

    def _iter_source_files(self, source_dir: Path) -> list[Path]:
        allowed_suffixes = (
            {".swift", *INTERFACE_BUILDER_EXTENSIONS}
            if self.config.scan_interface_builder
            else {".swift"}
        )
        matched: list[Path] = []
        for candidate in source_dir.rglob("*"):
            if not candidate.is_file() or candidate.suffix.lower() not in allowed_suffixes:
                continue
            relative_parts = candidate.relative_to(source_dir).parts[:-1]
            if any(part in self.config.excluded_dirs for part in relative_parts):
                continue
            matched.append(candidate)
        return sorted(matched)


def audit_assets(
    xcassets_dir: Path,
    source_dir: Path,
    *,
    config: ScannerConfig | None = None,
) -> AuditReport:
    """Convenience function to run `AssetScanner` with optional configuration."""
    return AssetScanner(config=config).scan(xcassets_dir, source_dir)
