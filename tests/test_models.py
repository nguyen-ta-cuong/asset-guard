import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from asset_guard.models import AssetIssue, AuditReport, DuplicateImage


@pytest.fixture
def make_report():
    def _make_report(
        *,
        total_assets: int = 10,
        used_assets_count: int = 8,
        issues: list[AssetIssue] | None = None,
        duplicate_images: list[DuplicateImage] | None = None,
    ) -> AuditReport:
        report = AuditReport(
            xcassets_path=Path("MyApp/Assets.xcassets"),
            source_path=Path("MyApp/Sources"),
            total_assets=total_assets,
            used_assets_count=used_assets_count,
        )
        if issues is not None:
            report.issues = issues
        if duplicate_images is not None:
            report.duplicate_images = duplicate_images
        return report

    return _make_report


def test_asset_issue_fields_and_frozen():
    path = Path("Assets.xcassets/app-icon.imageset")
    issue = AssetIssue(
        asset_name="app-icon",
        issue_type="missing_file",
        detail="icon@2x.png is missing",
        path=path,
    )

    assert issue.asset_name == "app-icon"
    assert issue.issue_type == "missing_file"
    assert issue.detail == "icon@2x.png is missing"
    assert issue.path == path
    with pytest.raises(FrozenInstanceError):
        issue.asset_name = "other"  # ty: ignore[invalid-assignment]


def test_duplicate_image_fields_and_frozen():
    files = [Path("a.png"), Path("b.png")]
    duplicate = DuplicateImage(
        sha256="deadbeef",
        file_size_bytes=2048,
        files=files,
    )

    assert duplicate.sha256 == "deadbeef"
    assert duplicate.file_size_bytes == 2048
    assert duplicate.files == files
    with pytest.raises(FrozenInstanceError):
        duplicate.sha256 = "cafebabe"  # ty: ignore[invalid-assignment]


@pytest.mark.parametrize(
    ("file_size_bytes", "file_count", "expected_wasted"),
    [
        (1024, 0, 0),
        (1024, 1, 0),
        (1024, 2, 1024),
        (512, 4, 1536),
        (0, 3, 0),
    ],
    ids=[
        "no_files",
        "single_file",
        "two_duplicates",
        "four_duplicates",
        "zero_byte_files",
    ],
)
def test_duplicate_image_wasted_space_bytes(file_size_bytes, file_count, expected_wasted):
    files = [Path(f"img_{index}.png") for index in range(file_count)]
    duplicate = DuplicateImage(
        sha256="deadbeef",
        file_size_bytes=file_size_bytes,
        files=files,
    )

    assert duplicate.wasted_space_bytes == expected_wasted


def test_audit_report_defaults_and_isolation():
    first = AuditReport(
        xcassets_path=Path("MyApp/Assets.xcassets"),
        source_path=Path("MyApp/Sources"),
        total_assets=10,
        used_assets_count=8,
    )
    second = AuditReport(
        xcassets_path=Path("OtherApp/Assets.xcassets"),
        source_path=Path("OtherApp/Sources"),
        total_assets=3,
        used_assets_count=3,
    )

    assert first.xcassets_path == Path("MyApp/Assets.xcassets")
    assert first.source_path == Path("MyApp/Sources")
    assert first.total_assets == 10
    assert first.used_assets_count == 8
    assert first.issues == []
    assert first.duplicate_images == []

    first.issues.append(
        AssetIssue(
            asset_name="banner",
            issue_type="unused",
            detail="Not referenced in source files",
            path=Path("Assets.xcassets/banner.imageset"),
        )
    )
    first.duplicate_images.append(
        DuplicateImage(
            sha256="aaa",
            file_size_bytes=500,
            files=[Path("a1.png"), Path("a2.png")],
        )
    )

    assert second.issues == []
    assert second.duplicate_images == []


def test_audit_report_unused_assets(make_report):
    empty_report = make_report()
    assert empty_report.unused_assets == []

    issues = [
        AssetIssue(
            asset_name="legacy_banner",
            issue_type="unused",
            detail="Not referenced in source files",
            path=Path("Assets.xcassets/legacy_banner.imageset"),
        ),
        AssetIssue(
            asset_name="broken_icon",
            issue_type="missing_file",
            detail="icon@2x.png not found",
            path=Path("Assets.xcassets/broken_icon.imageset"),
        ),
        AssetIssue(
            asset_name="bad_json",
            issue_type="invalid_json",
            detail="Malformed Contents.json",
            path=Path("Assets.xcassets/bad_json.imageset/Contents.json"),
        ),
        AssetIssue(
            asset_name="old_logo",
            issue_type="unused",
            detail="Not referenced in source files",
            path=Path("Assets.xcassets/old_logo.imageset"),
        ),
    ]
    report = make_report(issues=issues)

    assert report.unused_assets == ["legacy_banner", "old_logo"]


def test_audit_report_total_wasted_space_bytes(make_report):
    empty_report = make_report()
    assert empty_report.total_wasted_space_bytes == 0

    duplicates = [
        DuplicateImage(sha256="single", file_size_bytes=2000, files=[Path("only.png")]),
        DuplicateImage(
            sha256="pair",
            file_size_bytes=1000,
            files=[Path("a1.png"), Path("a2.png")],
        ),
        DuplicateImage(
            sha256="triple",
            file_size_bytes=250,
            files=[Path("b1.png"), Path("b2.png"), Path("b3.png")],
        ),
    ]
    report = make_report(duplicate_images=duplicates)

    assert report.total_wasted_space_bytes == 1500


def test_audit_report_to_dict_empty(make_report):
    report = make_report(total_assets=4, used_assets_count=4)

    payload = report.to_dict()

    assert payload == {
        "xcassets_path": "MyApp/Assets.xcassets",
        "source_path": "MyApp/Sources",
        "total_assets": 4,
        "summary": {
            "total_assets": 4,
            "used_assets_count": 4,
            "total_issues": 0,
            "total_duplicate_images": 0,
            "total_wasted_space_bytes": 0,
        },
        "issues": [],
        "duplicate_images": [],
    }
    assert json.loads(json.dumps(payload)) == payload


def test_audit_report_to_dict_populated(make_report):
    issue = AssetIssue(
        asset_name="logo",
        issue_type="invalid_json",
        detail="Malformed Contents.json",
        path=Path("MyApp/Assets.xcassets/logo.imageset/Contents.json"),
    )
    duplicate = DuplicateImage(
        sha256="aaa",
        file_size_bytes=1000,
        files=[Path("MyApp/a1.png"), Path("MyApp/a2.png"), Path("MyApp/a3.png")],
    )
    report = make_report(
        total_assets=5,
        used_assets_count=4,
        issues=[issue],
        duplicate_images=[duplicate],
    )

    payload = report.to_dict()

    assert payload == {
        "xcassets_path": "MyApp/Assets.xcassets",
        "source_path": "MyApp/Sources",
        "total_assets": 5,
        "summary": {
            "total_assets": 5,
            "used_assets_count": 4,
            "total_issues": 1,
            "total_duplicate_images": 1,
            "total_wasted_space_bytes": 2000,
        },
        "issues": [
            {
                "asset_name": "logo",
                "issue_type": "invalid_json",
                "detail": "Malformed Contents.json",
                "path": "MyApp/Assets.xcassets/logo.imageset/Contents.json",
            }
        ],
        "duplicate_images": [
            {
                "sha256": "aaa",
                "file_size_bytes": 1000,
                "files": ["MyApp/a1.png", "MyApp/a2.png", "MyApp/a3.png"],
                "wasted_space_bytes": 2000,
            }
        ],
    }
    assert json.loads(json.dumps(payload)) == payload
