"""Parse and validate `.imageset` folders inside an Xcode `.xcassets` catalog."""

import json
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

from asset_guard.config import ScannerConfig
from asset_guard.models import AssetIssue, IssueType


class ParsedImageset(NamedTuple):
    """Result of parsing one `.imageset` (unpacks like a 3-tuple)."""

    asset_name: str
    existing_files: list[Path]
    issues: list[AssetIssue]


def discover_catalogs(project_dir: Path, excluded_dirs: frozenset[str]) -> list[Path]:
    """Find every `.xcassets` catalog under a project, skipping dependency/build dirs."""
    found: list[Path] = []
    for dirpath, dirnames, _filenames in project_dir.walk():
        keep: list[str] = []
        for name in dirnames:
            if name in excluded_dirs:
                continue
            if name.endswith(".xcassets"):
                found.append(dirpath / name)
            else:
                keep.append(name)
        dirnames[:] = keep
    return sorted(found)


def find_imagesets(xcassets_dir: Path) -> list[Path]:
    """Return every `.imageset` directory under the catalog, sorted."""
    return sorted(candidate for candidate in xcassets_dir.rglob("*.imageset") if candidate.is_dir())


def _load_json_dict(contents_file: Path) -> dict[str, object] | None:
    try:
        parsed: object = json.loads(contents_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def resolve_asset_name(imageset_dir: Path, xcassets_dir: Path | None) -> str:
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


def parse_imageset(
    imageset_dir: Path,
    *,
    xcassets_dir: Path | None = None,
    config: ScannerConfig | None = None,
) -> ParsedImageset:
    """Parse an `.imageset` directory and validate files, vectors, and scales."""
    config = config or ScannerConfig()
    asset_name = resolve_asset_name(imageset_dir, xcassets_dir)
    contents_file = imageset_dir / "Contents.json"

    raw_images, load_issue = _load_imageset_entries(
        asset_name=asset_name,
        imageset_dir=imageset_dir,
        contents_file=contents_file,
    )
    if load_issue is not None:
        return ParsedImageset(asset_name, [], [load_issue])

    declared_entries = [
        entry
        for entry in raw_images
        if isinstance(entry.get("filename"), str) and str(entry["filename"]).strip()
    ]
    if not declared_entries:
        return ParsedImageset(
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

    existing_files, file_issues = _check_declared_files(
        asset_name=asset_name,
        imageset_dir=imageset_dir,
        declared_entries=declared_entries,
    )
    scale_issues = _check_vectors_and_scales(
        asset_name=asset_name,
        contents_file=contents_file,
        declared_entries=declared_entries,
        config=config,
    )
    return ParsedImageset(asset_name, existing_files, [*file_issues, *scale_issues])


def _load_imageset_entries(
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
    if not isinstance(images_obj, list) or not all(isinstance(item, dict) for item in images_obj):
        return [], AssetIssue(
            asset_name=asset_name,
            issue_type=IssueType.INVALID_JSON,
            detail="Contents.json 'images' field must be a list of objects",
            path=contents_file,
        )

    entries: list[dict[str, object]] = [item for item in images_obj if isinstance(item, dict)]
    return entries, None


def _check_declared_files(
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
    *,
    asset_name: str,
    contents_file: Path,
    declared_entries: list[dict[str, object]],
    config: ScannerConfig,
) -> list[AssetIssue]:
    vector_entries = [
        entry
        for entry in declared_entries
        if Path(str(entry["filename"])).suffix.lower() in config.vector_extensions
    ]

    if vector_entries:
        scaled_vectors = [str(entry["filename"]) for entry in vector_entries if "scale" in entry]
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
        missing_scales = sorted(config.expected_scales - scales_by_appearance[appearance])
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
