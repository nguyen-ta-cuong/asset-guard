"""Detect byte-identical image files (size pre-filter + SHA-256)."""

import hashlib
from collections import defaultdict
from pathlib import Path

from asset_guard.models import DuplicateImage

_MIN_GROUP_SIZE = 2


def compute_sha256(file_path: Path) -> str:
    """Compute the SHA-256 digest of a file using streaming I/O."""
    with file_path.open("rb") as file_handle:
        return hashlib.file_digest(file_handle, "sha256").hexdigest()


def find_duplicate_images(image_files: list[Path]) -> list[DuplicateImage]:
    """Group image files with identical byte content, largest waste first."""
    unique_files: dict[Path, Path] = {}
    for file_path in image_files:
        if file_path.is_file():
            unique_files.setdefault(file_path.resolve(), file_path)

    size_to_paths: dict[int, list[Path]] = defaultdict(list)
    for file_path in unique_files.values():
        size_to_paths[file_path.stat().st_size].append(file_path)

    duplicate_groups: list[DuplicateImage] = []
    for file_size_bytes, candidate_paths in size_to_paths.items():
        if len(candidate_paths) < _MIN_GROUP_SIZE:
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
            if len(paths) >= _MIN_GROUP_SIZE
        )

    return sorted(
        duplicate_groups,
        key=lambda group: (-group.wasted_space_bytes, group.sha256),
    )
