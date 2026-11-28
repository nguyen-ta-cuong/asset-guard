"""Find which asset names may be referenced from project sources.

Detection is deliberately *conservative*, because `clean` deletes whatever is reported
unused. An asset counts as used when:

- its exact name appears as a string literal in Swift / Objective-C code (covers
  `UIImage(named:)`, `Image("…")`, enums, constants, SwiftGen, R.swift, …);
- its name matches an interpolated / format string such as `"icon_\\(i)"` or
  `@"icon_%d"` (dynamic names);
- one of its possible Xcode-generated Swift symbols appears as `.symbol` or
  `ImageResource.symbol` / `UIImage.symbol` / `Image.symbol`, also nested under a
  namespace (`.Onboarding.hero`) or behind any capitalised type (`Asset.hero`);
- it lives in a `provides-namespace` folder and its bare name (`hero` for
  `onboarding/hero`) appears as a string, since the prefix may be concatenated;
- its name equals any attribute value in a storyboard / XIB, or any `<string>` in a plist.

False "used" results only mean an asset is kept. False "unused" results break apps.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from asset_guard.config import ScannerConfig

CODE_EXTENSIONS: frozenset[str] = frozenset({".swift", ".m", ".mm", ".h"})
INTERFACE_BUILDER_EXTENSIONS: frozenset[str] = frozenset({".storyboard", ".xib"})
PLIST_EXTENSIONS: frozenset[str] = frozenset({".plist"})

# Tokenizers split a file into string literals and skipped spans (comments, C char
# literals) in one left-to-right pass, so a quote inside a comment, or `//` inside a
# string, is never misread. A single-line literal cannot span lines: an unbalanced quote
# then costs one line at most, instead of inverting every later literal in the file.
_COMMENT = r"//[^\n]*|/\*[\s\S]*?\*/"
_STRING = r'"(?:\\.|[^"\\\n])*"'
_SWIFT_TOKEN = re.compile(
    r'(?P<block_hashes>#+)"""(?P<raw_block>[\s\S]*?)"""(?P=block_hashes)'  # #"""…"""#
    r'|"""(?P<block>(?:\\[\s\S]|[^\\])*?)"""'  # """…""" (may hold bare quotes)
    r'|(?P<raw_hashes>#+)"(?P<raw>[^\n]*?)"(?P=raw_hashes)'  # #"…"#
    rf"|(?P<plain>{_STRING})|{_COMMENT}"
)
# C / Objective-C: char literals such as `'"'` must not open a string.
_C_TOKEN = re.compile(rf"(?P<plain>{_STRING})|'(?:\\.|[^'\\\n])+'|{_COMMENT}")
_SWIFT_INTERPOLATION = re.compile(r"\\\((?:[^()]|\([^()]*\))*\)")
# printf-style specifiers: %d, %@, %02ld, %1$s, ...
_FORMAT_SPECIFIER = re.compile(
    r"%(?:\d+\$)?[-+ #0']*(?:\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|q|L|z|t|j)?[@dDiuUxXoOfFeEgGaAcCsSp]"
)
_MIN_PATTERN_LITERAL_CHARS = 2

# A member chain: `.logo`, `.Onboarding.hero`, `ImageResource.Onboarding.hero`, `foo.logo`.
# Which of its members count as symbols is decided in `_chain_symbols`.
_IDENTIFIER = r"[A-Za-z_]\w*"
_MEMBER_CHAIN = re.compile(
    rf"(?:(?<!\w)(?P<head>{_IDENTIFIER})\s*)?"
    rf"(?P<members>\.\s*{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER})*)"
)
_MEMBER = re.compile(rf"\.\s*({_IDENTIFIER})")
_CALL_FOLLOWS = re.compile(r"\s*\(")
# A dot written directly after one of these (or after a word character) reads a member
# of a value: `foo().logo`, `items[0].logo`, `view?.logo`, `view.logo`. After anything
# else, whitespace included, it starts an implicit member: `(.logo`, `? .logo`, `?? .logo`.
_VALUE_END = frozenset(")]?!")

_IB_ATTRIBUTE_VALUE = re.compile(r'=\s*"([^"]+)"')
_PLIST_STRING = re.compile(r"<string>([^<]+)</string>")

_SYMBOL_SPLIT_PATTERN = re.compile(r"[^A-Za-z0-9]+")
_LEADING_UPPER_RUN = re.compile(r"^([A-Z]+)(?=[A-Z][a-z]|$)")


@dataclass(frozen=True)
class References:
    """Everything in the sources that might point at an asset."""

    names: frozenset[str]
    patterns: tuple[re.Pattern[str], ...]
    symbols: frozenset[str]

    def uses(self, asset_name: str) -> bool:
        """Return True if `asset_name` may be referenced (conservative)."""
        # A namespaced asset is also kept by its bare name: `"onboarding/" + "hero"`.
        spellings = {asset_name, asset_name.rsplit("/", 1)[-1]}
        if not spellings.isdisjoint(self.names):
            return True
        if not swift_symbol_candidates(asset_name).isdisjoint(self.symbols):
            return True
        return any(
            pattern.fullmatch(spelling) for pattern in self.patterns for spelling in spellings
        )


def to_swift_symbol(asset_name: str) -> str:
    """Convert an asset catalog name into its Xcode 15+ lowerCamelCase Swift symbol."""
    parts = [part for part in _SYMBOL_SPLIT_PATTERN.split(asset_name) if part]
    if not parts:
        return ""
    first = parts[0]
    first_normalized = first[0].lower() + first[1:]
    rest = [part[0].upper() + part[1:] for part in parts[1:]]
    return "".join([first_normalized, *rest])


def swift_symbol_candidates(asset_name: str) -> set[str]:
    """Return every Swift symbol Xcode might generate for an asset (over-approximation)."""
    names = {asset_name, asset_name.rsplit("/", 1)[-1]}
    candidates: set[str] = set()
    for name in names:
        base = to_swift_symbol(name)
        if not base:
            continue
        variants = {base}
        upper_run = _LEADING_UPPER_RUN.match(name.lstrip("_-. "))
        if upper_run:
            # "ABCLogo" -> "abcLogo"
            run = upper_run.group(1)
            variants.add(run.lower() + to_swift_symbol(name)[len(run) :])
        if base.endswith("Image") and len(base) > len("Image"):
            variants.add(base.removesuffix("Image"))
        candidates.update(variants)
        candidates.update(f"_{variant}" for variant in variants if variant[0].isdigit())
    return candidates


def _literal_to_pattern(content: str) -> re.Pattern[str] | None:
    """Turn `"icon_\\(i)"` / `"icon_%d"` into a regex, or None if it is not dynamic."""
    if not (_SWIFT_INTERPOLATION.search(content) or _FORMAT_SPECIFIER.search(content)):
        return None
    marked = _FORMAT_SPECIFIER.sub("\0", _SWIFT_INTERPOLATION.sub("\0", content))
    literal_parts = marked.split("\0")
    if sum(char.isalnum() for part in literal_parts for char in part) < (
        _MIN_PATTERN_LITERAL_CHARS
    ):
        return None
    return re.compile(".*".join(re.escape(part) for part in literal_parts), re.DOTALL)


def _token_literals(match: re.Match[str]) -> list[str] | None:
    """Return the string contents of a token, or None for a comment / char literal."""
    groups = match.groupdict()
    plain = groups.get("plain")
    if plain is not None:
        return [plain[1:-1]]
    raw = groups.get("raw")
    if raw is not None:
        return [_unraw(raw, groups["raw_hashes"])]
    raw_block = groups.get("raw_block")
    if raw_block is not None:
        return _block_lines(_unraw(raw_block, groups["block_hashes"]))
    block = groups.get("block")
    return None if block is None else _block_lines(block)


def _unraw(content: str, hashes: str) -> str:
    r"""Rewrite raw-string interpolation `\#(x)` as `\(x)` so it becomes a pattern."""
    return content.replace(f"\\{hashes}(", "\\(")


def _block_lines(content: str) -> list[str]:
    """Each line of a multi-line string may be an asset name on its own."""
    return [line.strip() for line in content.splitlines()]


def _chain_symbols(code: str) -> set[str]:
    """Return every member that may be a generated asset symbol.

    A member counts when it starts an implicit member expression (`.logo`), or when it
    comes after a member that counts or after a capitalised name. So `.Onboarding.hero`,
    `ImageResource.Onboarding.hero` and `Asset.hero` give `hero`, while `view.icon` gives
    nothing. A member that is called (`.padding()`) is a method, never an asset.
    """
    symbols: set[str] = set()
    for chain in _MEMBER_CHAIN.finditer(code):
        head = chain.group("head")
        counts = head is not None and head[0].isupper()
        called_at = chain.end() if _CALL_FOLLOWS.match(code, chain.end()) else -1
        for member in _MEMBER.finditer(code, chain.start("members"), chain.end()):
            name = member.group(1)
            before = code[member.start() - 1] if member.start() else ""
            implicit = not (before in _VALUE_END or before.isalnum() or before == "_")
            if (counts or implicit) and member.end() != called_at:
                symbols.add(name)
                counts = True
            counts = counts or name[0].isupper()
    return symbols


def _scan_code(
    content: str,
    *,
    names: set[str],
    patterns: dict[str, re.Pattern[str]],
    symbols: set[str],
    is_swift: bool,
) -> None:
    code_chunks: list[str] = []
    last_end = 0
    for match in (_SWIFT_TOKEN if is_swift else _C_TOKEN).finditer(content):
        code_chunks.append(content[last_end : match.start()])
        last_end = match.end()
        literals = _token_literals(match)
        if literals is None:
            continue  # comment or char literal
        code_chunks.append('""')
        for literal in filter(None, literals):
            pattern = _literal_to_pattern(literal)
            if pattern is None:
                names.add(literal)
            else:
                patterns.setdefault(pattern.pattern, pattern)
    code_chunks.append(content[last_end:])

    if is_swift:
        symbols.update(_chain_symbols("".join(code_chunks)))


def iter_source_files(source_dir: Path, config: ScannerConfig) -> list[Path]:
    """Return sorted code/IB/plist files, pruning excluded directories and catalogs."""
    allowed = CODE_EXTENSIONS | PLIST_EXTENSIONS
    if config.scan_interface_builder:
        allowed |= INTERFACE_BUILDER_EXTENSIONS

    matched: list[Path] = []
    for dirpath, dirnames, filenames in source_dir.walk():
        dirnames[:] = [
            name
            for name in dirnames
            if name not in config.excluded_dirs and not name.endswith(".xcassets")
        ]
        matched.extend(
            dirpath / filename for filename in filenames if Path(filename).suffix.lower() in allowed
        )
    return sorted(matched)


def collect_used_references(
    source_dir: Path,
    config: ScannerConfig | None = None,
) -> References:
    """Collect every string, dynamic pattern, and Swift symbol that may reference an asset."""
    config = config or ScannerConfig()
    names: set[str] = set()
    patterns: dict[str, re.Pattern[str]] = {}
    symbols: set[str] = set()

    for file_path in iter_source_files(source_dir, config):
        suffix = file_path.suffix.lower()
        content = file_path.read_text(encoding="utf-8", errors="ignore")

        if suffix in CODE_EXTENSIONS:
            _scan_code(
                content,
                names=names,
                patterns=patterns,
                symbols=symbols,
                is_swift=suffix == ".swift",
            )
        elif suffix in INTERFACE_BUILDER_EXTENSIONS:
            names.update(match.group(1) for match in _IB_ATTRIBUTE_VALUE.finditer(content))
        else:
            names.update(match.group(1).strip() for match in _PLIST_STRING.finditer(content))

    return References(
        names=frozenset(names),
        patterns=tuple(patterns[key] for key in sorted(patterns)),
        symbols=frozenset(symbols),
    )
