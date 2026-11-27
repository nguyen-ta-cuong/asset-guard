from pathlib import Path

import pytest

from asset_guard.config import ScannerConfig
from asset_guard.references import (
    collect_used_references,
    iter_source_files,
    swift_symbol_candidates,
    to_swift_symbol,
)


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def sources(tmp_path: Path) -> Path:
    root = tmp_path / "Sources"
    root.mkdir()
    return root


def test_swift_string_literals_count_as_names_but_comments_do_not(sources):
    _write(
        sources,
        "Main.swift",
        """
        // UIImage(named: "commented_line")
        /* Image("commented_block") */
        let a = UIImage(named: "uikit_icon", in: .main, compatibleWith: nil)
        let b = Image("swiftui_banner")
        let c = #imageLiteral(resourceName: "literal_logo")
        let d = Image("https://not-a-comment")
        enum Img: String { case logo = "enum_logo" }
        let e = ImageAsset(name: "swiftgen_logo")
        let multi = \"\"\"
        line one
        \"\"\"
        """,
    )

    refs = collect_used_references(sources)

    for used in [
        "uikit_icon",
        "swiftui_banner",
        "literal_logo",
        "https://not-a-comment",
        "enum_logo",
        "swiftgen_logo",
    ]:
        assert refs.uses(used), used
    assert not refs.uses("commented_line")
    assert not refs.uses("commented_block")


def test_swift_symbols_implicit_and_explicit(sources):
    _write(
        sources,
        "View.swift",
        """
        let a = Image(.heroBanner)
        let b = UIImage(resource: .appLogo)
        imageView.image = UIImage.profilePlaceholder
        let c = ImageResource.tabHome
        let d = foo.notASymbol
        Text("x").padding()
        """,
    )

    refs = collect_used_references(sources)

    assert refs.uses("hero_banner")
    assert refs.uses("app-logo")
    assert refs.uses("profile_placeholder")
    assert refs.uses("tab_home")
    assert "notASymbol" not in refs.symbols
    assert "padding" not in refs.symbols


@pytest.mark.parametrize(
    ("code", "used", "unused"),
    [
        ('UIImage(named: "icon_\\(index)")', ["icon_1", "icon_20"], ["icon", "badge_1"]),
        ('Image("\\(theme)_logo")', ["dark_logo"], ["logo_dark"]),
        ('UIImage(named: String(format: "flag_%@", code))', ["flag_vn"], ["flag"]),
        ('Image("\\(a)_\\(b)")', [], ["any_name"]),  # too little literal text to match
    ],
)
def test_dynamic_names_become_patterns(sources, code, used, unused):
    _write(sources, "Dynamic.swift", code)

    refs = collect_used_references(sources)

    for name in used:
        assert refs.uses(name), name
    for name in unused:
        assert not refs.uses(name), name


def test_objective_c_literals_and_format_strings(sources):
    _write(
        sources,
        "Legacy.m",
        """
        // [UIImage imageNamed:@"objc_commented"];
        UIImage *a = [UIImage imageNamed:@"objc_logo"];
        UIImage *b = [UIImage imageNamed:[NSString stringWithFormat:@"step_%ld", (long)i]];
        """,
    )

    refs = collect_used_references(sources)

    assert refs.uses("objc_logo")
    assert refs.uses("step_3")
    assert not refs.uses("objc_commented")


def test_interface_builder_and_plist_values(sources):
    _write(
        sources,
        "Main.storyboard",
        """
        <imageView image="storyboard_hero"/>
        <tabBarItem selectedImage="tab_selected"/>
        <resources><image name="resource_image" width="10" height="10"/></resources>
        """,
    )
    _write(sources, "Card.xib", '<state key="normal" backgroundImage="xib_button"/>')
    _write(sources, "Info.plist", "<key>UIImageName</key><string>launch_logo</string>")

    refs = collect_used_references(sources)

    for name in ["storyboard_hero", "tab_selected", "resource_image", "xib_button", "launch_logo"]:
        assert refs.uses(name), name


def test_interface_builder_can_be_disabled(sources):
    _write(sources, "Main.storyboard", '<imageView image="storyboard_hero"/>')

    refs = collect_used_references(sources, ScannerConfig(scan_interface_builder=False))

    assert not refs.uses("storyboard_hero")


def test_iter_source_files_prunes_excluded_dirs_and_catalogs(sources):
    keep = _write(sources, "App/View.swift", "")
    _write(sources, "Pods/Lib/Vendor.swift", 'Image("vendor_ignored")')
    _write(sources, ".build/checkouts/Pkg/File.swift", "")
    _write(sources, "Assets.xcassets/Fake.swift", "")
    _write(sources, "App/notes.txt", "")

    assert iter_source_files(sources, ScannerConfig()) == [keep]
    assert not collect_used_references(sources).uses("vendor_ignored")


def test_to_swift_symbol_conversions():
    assert to_swift_symbol("hero_banner") == "heroBanner"
    assert to_swift_symbol("app-logo") == "appLogo"
    assert to_swift_symbol("MyIcon") == "myIcon"
    assert to_swift_symbol("---") == ""


@pytest.mark.parametrize(
    ("asset_name", "expected"),
    [
        ("backgroundImage", {"backgroundImage", "background"}),
        ("ABCLogo", {"aBCLogo", "abcLogo"}),
        ("1st_place", {"1stPlace", "_1stPlace"}),
        ("Icons/arrow", {"iconsArrow", "arrow"}),
        ("---", set()),
    ],
)
def test_swift_symbol_candidates(asset_name, expected):
    assert swift_symbol_candidates(asset_name) == expected
