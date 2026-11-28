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
    "code",
    [
        "Image(.Onboarding.hero)",
        "let a = ImageResource.Onboarding.hero",
        "let a = UIImage(resource: .Onboarding.hero)",
        "Image(\n    .Onboarding\n    .hero\n)",
        "typealias O = ImageResource.Onboarding\nlet a = O.hero",
        "let a = Asset.Onboarding.hero.image",
        "let a = self.theme.Onboarding.hero",
    ],
)
def test_nested_namespace_symbols_keep_the_asset(sources, code):
    _write(sources, "View.swift", code)

    refs = collect_used_references(sources)

    assert refs.uses("onboarding/hero")
    assert not refs.uses("onboarding/other")


@pytest.mark.parametrize(
    ("code", "symbols"),
    [
        ("let a = flag ? .logoA : .logoB", {"logoA", "logoB"}),
        ("let a = cache[key] ?? .placeholder", {"placeholder"}),
        ("let a = flag ? .Onboarding.hero : .Onboarding.welcome", {"hero", "welcome"}),
        ("let a: ImageResource = flag\n    ? .logoA\n    : .logoB", {"logoA", "logoB"}),
        ("let a = Image . spaced", {"spaced"}),
        ("let a = images.map { $0.Image.logo }", {"logo"}),
        ("make()\n    .modifier\n    .trailing", {"modifier", "trailing"}),
        ("let a = [.first, .second]", {"first", "second"}),
    ],
)
def test_implicit_members_after_operators_and_line_breaks(sources, code, symbols):
    _write(sources, "View.swift", code)

    assert symbols <= collect_used_references(sources).symbols


def test_deeply_nested_namespace_symbol(sources):
    _write(sources, "View.swift", "Image(.OnboardingFlow.StepOne.bigHero)")

    refs = collect_used_references(sources)

    assert refs.uses("Onboarding Flow/Step One/big hero")
    assert not refs.uses("Onboarding Flow/Step One/small hero")


@pytest.mark.parametrize(
    "code",
    [
        "let a = view.hero",  # property of a value, not a type member
        "let a = make().hero",
        "let a = items[0].hero",
        "let a = model?.hero",
        "let a = model!.hero",
        "let a = pair.0.hero",
        "let a = Onboarding.hero()",  # method call
        "view.Onboarding.hero()",
        "// Image(.Onboarding.hero)",
        "/* ImageResource.Onboarding.hero */",
        'let a = "ImageResource.Onboarding.heroic"',
    ],
)
def test_members_that_are_not_asset_symbols(sources, code):
    _write(sources, "View.swift", code)

    refs = collect_used_references(sources)

    assert "hero" not in refs.symbols
    assert not refs.uses("onboarding/hero")


@pytest.mark.parametrize(
    ("code", "used", "unused"),
    [
        ('Image("onboarding/hero")', ["onboarding/hero"], ["onboarding/other", "hero2"]),
        # The prefix may be concatenated, so the bare name keeps a namespaced asset.
        ('Image("onboarding/" + "hero")', ["onboarding/hero", "hero"], ["onboarding/other"]),
        ('Image(folder + "/step_\\(i)")', [], ["onboarding/hero"]),
        ('Image(folder + "step_\\(i)")', ["onboarding/step_1", "step_2"], ["onboarding/hero"]),
        ('Image("onboarding/\\(name)")', ["onboarding/hero"], ["hero", "other/hero"]),
        # A full name never keeps a different asset that only shares the bare name.
        ('Image("settings/hero")', ["settings/hero"], ["onboarding/hero", "hero"]),
    ],
)
def test_namespaced_names_in_strings(sources, code, used, unused):
    _write(sources, "View.swift", code)

    refs = collect_used_references(sources)

    for name in used:
        assert refs.uses(name), name
    for name in unused:
        assert not refs.uses(name), name


def test_objc_and_interface_builder_namespaced_names(sources):
    _write(sources, "Legacy.m", 'UIImage *a = [UIImage imageNamed:@"onboarding/hero"];')
    _write(sources, "Main.storyboard", '<imageView image="settings/gear"/>')

    refs = collect_used_references(sources)

    assert refs.uses("onboarding/hero")
    assert refs.uses("settings/gear")
    assert not refs.uses("onboarding/gear")


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


def test_quote_inside_multiline_string_does_not_hide_later_literals(sources):
    _write(
        sources,
        "Help.swift",
        """
        let help = \"\"\"
        The screen is 5" wide
        multiline_name
        multiline_\\(index)
        \"\"\"
        let a = Image("after_multiline")
        // Image("commented_after_multiline")
        let b = UIImage(named: "second_after_multiline")
        """,
    )

    refs = collect_used_references(sources)

    assert refs.uses("after_multiline")
    assert refs.uses("second_after_multiline")
    assert refs.uses("multiline_name")
    assert refs.uses("multiline_7")
    assert not refs.uses("commented_after_multiline")
    assert not refs.uses("let a = Image(")
    assert not refs.uses("never_mentioned")


def test_swift_raw_strings_keep_the_tokenizer_in_sync(sources):
    _write(
        sources,
        "Raw.swift",
        """
        let quoted = #"She said "hi" and left"#
        let a = Image(#"raw_name"#)
        let b = Image(#"raw_\\#(index)"#)
        let c = ##"double "# hash"##
        let block = #\"\"\"
        An odd " quote and a fake end \"\"\" here
        raw_block_name
        raw_block_\\#(index)
        \"\"\"#
        let d = Image("after_raw")
        // Image("commented_after_raw")
        """,
    )

    refs = collect_used_references(sources)

    for used in ["raw_name", "raw_3", "raw_block_name", "raw_block_9", "after_raw"]:
        assert refs.uses(used), used
    assert not refs.uses("commented_after_raw")
    assert not refs.uses("never_mentioned")


def test_objc_char_literal_quote_does_not_hide_later_literals(sources):
    _write(
        sources,
        "Chars.m",
        """
        char quote = '"';
        char apostrophe = '\\'';
        UIImage *a = [UIImage imageNamed:@"after_char_literal"];
        // [UIImage imageNamed:@"commented_after_char"];
        UIImage *b = [UIImage imageNamed:@"second_after_char"];
        """,
    )

    refs = collect_used_references(sources)

    assert refs.uses("after_char_literal")
    assert refs.uses("second_after_char")
    assert not refs.uses("commented_after_char")
    assert not refs.uses("never_mentioned")


def test_unbalanced_quote_only_affects_its_own_line(sources):
    _write(
        sources,
        "Broken.swift",
        """
        let broken = "unterminated
        let a = Image("next_line_name")
        // Image("commented_after_broken")
        """,
    )

    refs = collect_used_references(sources)

    assert refs.uses("next_line_name")
    assert not refs.uses("commented_after_broken")


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
