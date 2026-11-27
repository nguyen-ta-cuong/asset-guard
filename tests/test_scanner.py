import hashlib
import json
from pathlib import Path

import pytest

from asset_guard import scanner
from asset_guard.models import IssueType
from asset_guard.scanner import AssetScanner, ScannerConfig, audit_assets, compute_sha256


def _create_imageset(
    parent: Path,
    name: str,
    contents: object,
    files: dict[str, bytes] | None = None,
) -> Path:
    imageset_dir = parent / f"{name}.imageset"
    imageset_dir.mkdir(parents=True, exist_ok=True)
    if contents is not None:
        raw_text = contents if isinstance(contents, str) else json.dumps(contents)
        (imageset_dir / "Contents.json").write_text(raw_text, encoding="utf-8")
    for filename, data in (files or {}).items():
        (imageset_dir / filename).write_bytes(data)
    return imageset_dir


def test_compute_sha256_matches_hashlib(tmp_path):
    target = tmp_path / "sample.bin"
    payload = b"asset-guard-sha256-payload" * 100
    target.write_bytes(payload)

    assert compute_sha256(target) == hashlib.sha256(payload).hexdigest()


def test_parse_valid_modern_ios_bitmap_2x_and_3x(tmp_path):
    imageset = _create_imageset(
        tmp_path,
        "hero_banner",
        {
            "images": [
                {"filename": "hero@2x.png", "idiom": "universal", "scale": "2x"},
                {"filename": "hero@3x.png", "idiom": "universal", "scale": "3x"},
            ]
        },
        files={"hero@2x.png": b"2x", "hero@3x.png": b"3x"},
    )

    name, existing_files, issues = AssetScanner().parse_imageset(imageset)

    assert name == "hero_banner"
    assert len(existing_files) == 2
    assert issues == []


@pytest.mark.parametrize("filename", ["icon.svg", "icon.pdf", "ICON.SVG"])
def test_parse_valid_single_scale_vector_images(tmp_path, filename):
    imageset = _create_imageset(
        tmp_path,
        "vector_icon",
        {"images": [{"filename": filename, "idiom": "universal"}]},
        files={filename: b"<svg/>"},
    )

    name, existing_files, issues = AssetScanner().parse_imageset(imageset)

    assert name == "vector_icon"
    assert existing_files == [imageset / filename]
    assert issues == []


@pytest.mark.parametrize("filename", ["badge.svg", "badge.pdf"])
def test_parse_vector_with_explicit_scale_reports_invalid_vector(tmp_path, filename):
    imageset = _create_imageset(
        tmp_path,
        "scaled_vector",
        {"images": [{"filename": filename, "idiom": "universal", "scale": "2x"}]},
        files={filename: b"vector"},
    )

    _name, _files, issues = AssetScanner().parse_imageset(imageset)

    assert len(issues) == 1
    assert issues[0].issue_type == IssueType.INVALID_VECTOR
    assert filename in issues[0].detail


def test_parse_bitmap_missing_3x_and_dark_appearance_scales(tmp_path):
    imageset = _create_imageset(
        tmp_path,
        "button_bg",
        {
            "images": [
                {"filename": "bg@2x.png", "idiom": "universal", "scale": "2x"},
                {
                    "filename": "bg_dark@2x.png",
                    "idiom": "universal",
                    "scale": "2x",
                    "appearances": [
                        {"appearance": "luminosity", "value": "dark"},
                        "ignored-non-dict",
                        {"appearance": "contrast", "value": ""},
                    ],
                },
            ]
        },
        files={"bg@2x.png": b"light2x", "bg_dark@2x.png": b"dark2x"},
    )

    _name, _files, issues = AssetScanner().parse_imageset(imageset)

    assert len(issues) == 2
    assert all(issue.issue_type == IssueType.INCOMPLETE_SCALES for issue in issues)
    assert "Missing required scales: 3x" in issues[0].detail
    assert "Missing required scales for dark appearance: 3x" in issues[1].detail


def test_parse_bitmap_without_scale_key_reports_incomplete_scales(tmp_path):
    imageset = _create_imageset(
        tmp_path,
        "unscaled_png",
        {"images": [{"filename": "raw.png", "idiom": "universal"}]},
        files={"raw.png": b"png"},
    )

    _name, _files, issues = AssetScanner().parse_imageset(imageset)

    assert len(issues) == 1
    assert issues[0].issue_type == IssueType.INCOMPLETE_SCALES
    assert "2x, 3x" in issues[0].detail


def test_parse_invalid_and_missing_contents_json(tmp_path):
    missing = _create_imageset(tmp_path, "no_json", contents=None)
    bad_syntax = _create_imageset(tmp_path, "bad_syntax", contents="{invalid")
    non_object = _create_imageset(tmp_path, "non_object", contents=["not", "dict"])
    bad_images = _create_imageset(tmp_path, "bad_images", contents={"images": "wrong"})
    bad_entry = _create_imageset(tmp_path, "bad_entry", contents={"images": ["not-dict"]})

    asset_scanner = AssetScanner()
    for target in (missing, bad_syntax, non_object, bad_images, bad_entry):
        _name, files, issues = asset_scanner.parse_imageset(target)
        assert files == []
        assert len(issues) == 1
        assert issues[0].issue_type == IssueType.INVALID_JSON


def test_parse_empty_imageset_reports_issue(tmp_path):
    placeholder = _create_imageset(
        tmp_path,
        "empty_placeholder",
        {
            "images": [
                {"idiom": "universal", "scale": "2x"},
                {"idiom": "universal", "scale": "3x", "filename": "   "},
            ]
        },
    )

    _name, files, issues = AssetScanner().parse_imageset(placeholder)

    assert files == []
    assert len(issues) == 1
    assert issues[0].issue_type == IssueType.EMPTY_IMAGESET


def test_parse_missing_files_and_deduplicates_shared_file_entries(tmp_path):
    imageset = _create_imageset(
        tmp_path,
        "shared_icon",
        {
            "images": [
                {"filename": "shared@2x.png", "idiom": "universal", "scale": "2x"},
                {
                    "filename": "shared@2x.png",
                    "idiom": "ipad",
                    "scale": "2x",
                },
                {"filename": "missing@3x.png", "idiom": "universal", "scale": "3x"},
                {"filename": "missing@3x.png", "idiom": "ipad", "scale": "3x"},
            ]
        },
        files={"shared@2x.png": b"same-content"},
    )

    asset_scanner = AssetScanner()
    _name, existing_files, issues = asset_scanner.parse_imageset(imageset)

    assert existing_files == [imageset / "shared@2x.png"]
    assert len(issues) == 1
    assert issues[0].issue_type == IssueType.MISSING_FILE
    assert issues[0].detail.count("missing@3x.png") == 1
    assert asset_scanner.find_duplicate_images(existing_files) == []


def test_find_duplicate_images_prefilters_by_size_and_sorts_by_waste(tmp_path, monkeypatch):
    unique_size = tmp_path / "unique.png"
    unique_size.write_bytes(b"12345")

    same_size_a = tmp_path / "diff_a.png"
    same_size_b = tmp_path / "diff_b.png"
    same_size_a.write_bytes(b"aaaa")
    same_size_b.write_bytes(b"bbbb")

    small_dup_1 = tmp_path / "small_1.png"
    small_dup_2 = tmp_path / "small_2.png"
    small_dup_1.write_bytes(b"xx")
    small_dup_2.write_bytes(b"xx")

    large_dup_1 = tmp_path / "large_1.png"
    large_dup_2 = tmp_path / "large_2.png"
    large_dup_3 = tmp_path / "large_3.png"
    large_bytes = b"L" * 100
    large_dup_1.write_bytes(large_bytes)
    large_dup_2.write_bytes(large_bytes)
    large_dup_3.write_bytes(large_bytes)

    missing_file = tmp_path / "missing.png"

    hashed_paths: list[Path] = []
    real_compute = scanner.compute_sha256

    def _spy_sha256(path: Path) -> str:
        hashed_paths.append(path)
        return real_compute(path)

    monkeypatch.setattr(scanner, "compute_sha256", _spy_sha256)

    groups = AssetScanner().find_duplicate_images(
        [
            unique_size,
            same_size_a,
            same_size_b,
            small_dup_1,
            small_dup_2,
            small_dup_1,  # duplicate path entry should be ignored
            large_dup_1,
            large_dup_2,
            large_dup_3,
            missing_file,
        ]
    )

    assert unique_size not in hashed_paths
    assert len(groups) == 2
    assert groups[0].wasted_space_bytes == 200
    assert groups[0].files == sorted([large_dup_1, large_dup_2, large_dup_3])
    assert groups[1].wasted_space_bytes == 2
    assert groups[1].files == sorted([small_dup_1, small_dup_2])


def test_namespace_resolution_and_duplicate_asset_name(tmp_path):
    xcassets = tmp_path / "Assets.xcassets"
    icons_dir = xcassets / "Icons"
    icons_dir.mkdir(parents=True)
    (icons_dir / "Contents.json").write_text(
        json.dumps({"properties": {"provides-namespace": True}}),
        encoding="utf-8",
    )

    broken_folder = xcassets / "BrokenGroup"
    broken_folder.mkdir()
    (broken_folder / "Contents.json").write_text("{bad-json", encoding="utf-8")

    plain_folder = xcassets / "PlainGroup"
    plain_folder.mkdir()

    _create_imageset(
        icons_dir,
        "arrow",
        {"images": [{"filename": "arrow.svg", "idiom": "universal"}]},
        files={"arrow.svg": b"<svg id='1'/>"},
    )
    _create_imageset(
        broken_folder,
        "shared",
        {"images": [{"filename": "shared.pdf", "idiom": "universal"}]},
        files={"shared.pdf": b"%PDF-1"},
    )
    _create_imageset(
        plain_folder,
        "shared",
        {"images": [{"filename": "shared.pdf", "idiom": "universal"}]},
        files={"shared.pdf": b"%PDF-2"},
    )

    sources = tmp_path / "Sources"
    sources.mkdir()
    (sources / "View.swift").write_text('Image("Icons/arrow")\nImage("shared")\n', encoding="utf-8")

    # Outside xcassets_dir gracefully falls back to stem
    outside = _create_imageset(
        tmp_path / "Other",
        "external",
        {"images": [{"filename": "ext.svg", "idiom": "universal"}]},
        files={"ext.svg": b"<svg/>"},
    )
    outside_name, _files, _issues = AssetScanner().parse_imageset(outside, xcassets_dir=xcassets)
    assert outside_name == "external"

    report = AssetScanner().scan(xcassets, sources)

    assert report.total_assets == 2
    assert report.used_assets_count == 2
    assert any(issue.issue_type == IssueType.DUPLICATE_ASSET_NAME for issue in report.issues)


def test_collect_used_references_swift_comments_symbols_ib_and_excludes(tmp_path):
    sources = tmp_path / "Sources"
    sources.mkdir()
    (sources / "Main.swift").write_text(
        """
        import SwiftUI
        import UIKit

        // UIImage(named: "commented_line")
        /* Image("commented_block") */
        let a = UIImage(named: "uikit_icon", in: .main, compatibleWith: nil)
        let b = Image("swiftui_banner")
        let c = Image(decorative: "bg_pattern")
        let d = #imageLiteral(resourceName: "literal_logo")
        let e = Image(systemName: "star.fill")
        let f = Image(.heroBanner)
        let g = UIImage(resource: .appLogo)
        let h = Image("https://not-a-comment")
        """,
        encoding="utf-8",
    )
    (sources / "Launch.storyboard").write_text(
        '<imageView image="storyboard_hero" />',
        encoding="utf-8",
    )
    (sources / "Card.xib").write_text(
        '<state key="normal" image="xib_button" />',
        encoding="utf-8",
    )
    pods_dir = sources / "Pods" / "Lib"
    pods_dir.mkdir(parents=True)
    (pods_dir / "Vendor.swift").write_text('Image("vendor_ignored")', encoding="utf-8")

    asset_scanner = AssetScanner()
    names, symbols = asset_scanner.collect_used_references(sources)

    assert names == {
        "uikit_icon",
        "swiftui_banner",
        "bg_pattern",
        "literal_logo",
        "https://not-a-comment",
        "storyboard_hero",
        "xib_button",
    }
    assert symbols == {"heroBanner", "appLogo"}
    assert asset_scanner.collect_used_assets(sources) == names

    swift_only = AssetScanner(ScannerConfig(scan_interface_builder=False))
    swift_names = swift_only.collect_used_assets(sources)
    assert "storyboard_hero" not in swift_names
    assert "xib_button" not in swift_names


def test_to_swift_symbol_conversions():
    assert scanner._to_swift_symbol("hero_banner") == "heroBanner"  # noqa: SLF001
    assert scanner._to_swift_symbol("app-logo") == "appLogo"  # noqa: SLF001
    assert scanner._to_swift_symbol("MyIcon") == "myIcon"  # noqa: SLF001
    assert scanner._to_swift_symbol("---") == ""  # noqa: SLF001


def test_scan_raises_for_missing_directories(tmp_path):
    valid_dir = tmp_path / "Valid"
    valid_dir.mkdir()
    missing_dir = tmp_path / "Missing"

    with pytest.raises(FileNotFoundError, match="Asset catalog directory not found"):
        AssetScanner().scan(missing_dir, valid_dir)

    with pytest.raises(FileNotFoundError, match="Source directory not found"):
        AssetScanner().scan(valid_dir, missing_dir)


def test_audit_assets_end_to_end(tmp_path):
    xcassets = tmp_path / "Assets.xcassets"
    sources = tmp_path / "Sources"
    xcassets.mkdir()
    sources.mkdir()

    shared_bytes = b"duplicate-image-bytes-123456"
    _create_imageset(
        xcassets,
        "hero_banner",
        {
            "images": [
                {"filename": "hero@2x.png", "idiom": "universal", "scale": "2x"},
                {"filename": "hero@3x.png", "idiom": "universal", "scale": "3x"},
            ]
        },
        files={"hero@2x.png": shared_bytes, "hero@3x.png": b"unique-3x"},
    )
    _create_imageset(
        xcassets,
        "unused_vector",
        {"images": [{"filename": "icon.svg", "idiom": "universal"}]},
        files={"icon.svg": shared_bytes},
    )
    (sources / "HomeView.swift").write_text("let v = Image(.heroBanner)\n", encoding="utf-8")

    report = audit_assets(xcassets, sources)

    assert report.xcassets_path == xcassets
    assert report.source_path == sources
    assert report.total_assets == 2
    assert report.used_assets_count == 1
    assert report.unused_assets == ["unused_vector"]
    assert len(report.duplicate_images) == 1
    assert report.total_wasted_space_bytes == len(shared_bytes)
