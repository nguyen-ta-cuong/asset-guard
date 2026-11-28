# AGENTS.md

Guidance for AI coding agents (and humans) working on **asset-guard**.

## Read this first

- **Prime directive: never report a used asset as unused.** `clean --apply` deletes
  whatever is reported unused, and a missing image is a runtime bug the compiler does
  not catch. A false "used" only keeps a few kilobytes. When in doubt, count the asset
  as used.
- **Definition of done:** `make lint && make test` both pass. New behavior has a
  positive and a negative test. Docs that describe the behavior are updated in the same
  change (see [Change checklists](#change-checklists)).
- **Do not weaken** the [removal safety rules](#removal-safety-rules-enforced-do-not-weaken),
  the [JSON contract](#json-report-public-contract), or the [exit codes](#exit-codes).
  CI pipelines of other teams depend on them.
- **Do not add a runtime dependency** without asking. See [Dependencies](#dependencies).

## What the tool does

`asset-guard` is a Python CLI for iOS teams. It scans Xcode asset catalogs
(`.xcassets`) in CI **before the app is built**. It needs no Xcode and runs on Linux.

| Command | Use | Effect |
| --- | --- | --- |
| `audit` | PR gate: `audit . --fail-on unused,duplicates` | Reports only. Never modifies files. |
| `clean` | Pre-build: `clean . --apply`, then `xcodebuild` | Deletes unused `.imageset` folders from the checkout. Dry run without `--apply`. |

Findings fall into three categories that `--fail-on` can gate on:

| Category | Contains |
| --- | --- |
| `unused` | `IssueType.UNUSED`: an image set nothing references |
| `duplicates` | byte-identical image files (`DuplicateImage` groups) **plus** `IssueType.DUPLICATE_ASSET_NAME` |
| `broken` | every other `IssueType`: `invalid_json`, `empty_imageset`, `missing_file`, `invalid_vector`, `incomplete_scales` |

## Commands

The toolchain is `uv`, Python ≥ 3.12 (`.python-version` pins 3.12).

| Task | Command |
| --- | --- |
| Install all deps | `make dev` (`uv sync --all-groups`) |
| Lint + type-check | `make lint` (`ruff format --check`, `ruff check`, `ty check src/`) |
| Auto-format | `make format` |
| All tests + coverage gate | `make test` |
| One file or one test | `uv run pytest tests/test_references.py -k dynamic --no-cov` |
| Build sdist + wheel | `make build` |
| Run the CLI from source | `uv run asset-guard audit examples/SampleApp` |

`--no-cov` matters for partial runs: the coverage gate (≥ 80%, branch coverage,
currently ~99%) is in `addopts`, so a subset of tests prints `FAIL Required test
coverage` even when every test passed.

CLI surface (both commands): `[PROJECT]`, `--xcassets P` (repeatable), `--source P`,
`--config P`, `--ignore GLOB` (repeatable), `--format text|json`, `--fail-on LIST`,
`--fail-on-issues` (same as `--fail-on any`), `--report-json P`, `--summary-file P`,
`--no-color`, `-v`. `clean` adds `--apply` and `--force`. The root has `--version`/`-V`.

## Layout and data flow

```text
src/asset_guard/
├── cli.py          # Typer app (audit, clean): path/config resolution, git safety, outputs, exit codes
├── config.py       # ScannerConfig, defaults, .asset-guard.toml loading (tomllib), ignore globs
├── models.py       # All shared data models and the JSON shape (pure, no I/O)
├── catalog.py      # .xcassets side: discover catalogs, find .imageset dirs, resolve names, validate
├── references.py   # Source side: Swift/ObjC strings, dynamic patterns, Swift symbols, IB attributes, plists
├── duplicates.py   # Size pre-filter + SHA-256 grouping of identical files
├── scanner.py      # AssetScanner orchestrator: catalogs + references + duplicates -> AuditReport
├── cleaner.py      # plan_removal, apply_removal (the ONLY place that deletes), git worktree check
└── reporting.py    # Rich text (audit/clean) and Markdown summary rendering
tests/                          # see "Testing" for which file covers which module
action.yml                      # composite GitHub Action; runs this repo's source with uvx
.github/workflows/ci.yml        # lint/test + action smoke test against examples/SampleApp
.github/workflows/publish.yml   # PyPI Trusted Publishing (OIDC) when a GitHub Release is published
examples/SampleApp/             # tiny fake Xcode project, one asset per reference style
docs/ci.md                      # user docs: GitHub Actions, Jenkins, exit codes, safety check
docs/private/                   # gitignored personal notes; never commit or link to them
```

```text
cli.audit / cli.clean ─▶ _prepare (paths, config, --fail-on) ─▶ _scan ─▶ run_audit ─▶ AssetScanner.scan
                                                                    ├─ catalog.find_imagesets / parse_imageset (per catalog)
                                                                    ├─ references.collect_used_references → References.uses(name)
                                                                    └─ duplicates.find_duplicate_images
   clean only: plan_removal(report) ─▶ worktree_state ─▶ apply_removal ─▶ _scan again
   output: reporting.* to stdout, JSON to stdout / --report-json, Markdown to --summary-file
   last step: _exit_for_findings (exit 1 if a --fail-on category has findings)
```

Dependency direction: `cli → {scanner, cleaner, reporting} → {catalog, references,
duplicates} → {config, models}`. `models` and `config` import nothing from the
package. Keep it that way, with no cycles.

### Where new work goes

| Feature | Module |
| --- | --- |
| New reference syntax / file type | `references.py` (keep it conservative) |
| New catalog check (idioms, colorsets…) | `catalog.py`, new `IssueType` member and its `category` |
| New config key | `config.py` (`CONFIG_KEYS`, validation, `load_scanner_config`) |
| New removal behavior | `cleaner.py` (deletion only in `apply_removal`) |
| New CLI option | `cli.py`: define it once as an `Annotated` alias and share it between commands |
| New output format (SARIF, JUnit…) | `reporting.py`, wired through `_write_outputs` |
| New action input/output | `action.yml`, passed via `env:` (never interpolate `${{ }}` into `run:`) |

## Business rules

These are easy to get wrong from a quick read of the code.

### Path and config resolution (`cli._prepare`)

- `PROJECT` defaults to the current directory. Without `--source`, sources are scanned
  from `PROJECT`. Without `--xcassets`, every `*.xcassets` directory under `PROJECT` is
  discovered. Finding none is exit `2`.
- Config is auto-loaded only from `PROJECT/.asset-guard.toml`. There is no upward
  search and no fallback to the current directory. `--config` overrides it.
- Config keys: `ignore`, `exclude-dirs`, `expected-scales`, `scan-interface-builder`.
  Unknown keys and wrong types raise `ConfigError` (exit `2`), so a typo cannot pass
  silently.
- `exclude-dirs` **adds to** the defaults (`.git .build .swiftpm DerivedData Pods
  Carthage`). It matches directory **names** at any depth, not paths, and it applies to
  both catalog discovery and the source scan.
- `--ignore` globs are appended to the config's `ignore`. Matching is `fnmatchcase`
  on the full asset name, including any namespace prefix (`Icons/star`).

### Asset names (`catalog.resolve_asset_name`)

- The name is the `.imageset` folder name without the suffix. Parent folders are
  prefixed (`Folder/name`) only when their `Contents.json` sets
  `properties.provides-namespace` to `true`.
- Image sets are found at any depth inside a catalog. Catalogs are not nested: the
  walk does not descend into a `.xcassets` directory when it looks for more catalogs.
- `DUPLICATE_ASSET_NAME` is checked **per catalog**. The same name in two catalogs is
  normal (app plus widget) and is not reported. Only the first definition of a
  duplicated name counts toward `total_assets`.

### Classifying an asset (`AssetScanner.scan`)

The order is: referenced → **used**; otherwise matches an ignore glob → **ignored**;
otherwise → **unused**. So `ignored_assets` lists only assets that are both
unreferenced and ignored, and `total_assets == used + ignored + unused`.

Broken image sets are still classified. An image set with invalid JSON can also be
unused, and `clean` will then delete it.

### Validation (`catalog.parse_imageset`)

- A missing, unparsable, or non-object `Contents.json`, or an `images` field that is
  not a list of objects, gives `INVALID_JSON` and stops further checks for that set.
- No entry with a non-empty `filename` gives `EMPTY_IMAGESET`.
- If any entry is a vector (`.pdf`, `.svg`), scale checks are skipped. The only vector
  rule is that no vector entry may have a `scale` key (`INVALID_VECTOR`).
- Bitmaps need every scale in `expected-scales` (default `2x`, `3x`) **per appearance
  variant** (light, dark, …). Each variant with a gap gets its own `INCOMPLETE_SCALES`.

### Duplicates (`duplicates.find_duplicate_images`)

Only files declared in a `Contents.json` and present on disk are hashed. Files are
de-duplicated by resolved path first, so a symlink or a file listed twice is not a
duplicate of itself. Groups sort by wasted bytes (largest first), then by hash.

### `clean`

- The plan includes `UNUSED` issues whose path ends in `.imageset`, is a directory,
  and resolves strictly inside an audited catalog.
- `applied` is true only when `--apply` was passed **and** the plan is not empty. With
  nothing to remove, the git check is skipped and `clean.applied` is `false`.
- After deletion the project is scanned again. The JSON `summary` and `--fail-on`
  describe what **remains**; the `clean` section describes what was removed. So
  `clean --apply --fail-on unused` normally passes.
- The git check runs `git status --porcelain --untracked-files=all` on the catalogs
  only. Dirty source files do not block `--apply`.

## Reference detection is conservative, by design

`References.uses(name)` returns True if the asset **might** be used:

- **Exact name:** any string literal in `.swift/.m/.mm/.h` (comments are stripped),
  any `="value"` attribute in `.storyboard/.xib`, or any `<string>` in a `.plist`.
  The API around the literal is not inspected, which is what makes enums, constants,
  SwiftGen, and R.swift work without special cases.
- **Dynamic pattern:** a literal with Swift interpolation or a printf specifier becomes
  a full-match regex (`"icon_\(i)"`, `@"icon_%d"` → `icon_.*`). A pattern needs ≥ 2
  alphanumeric literal characters, otherwise `"\(a)_\(b)"` would match everything.
- **Generated Swift symbol** (`.swift` files only): any `swift_symbol_candidates(name)`
  appearing as an implicit member (`.logo`) or as
  `ImageResource|UIImage|NSImage|Image.logo`. Candidates over-approximate Xcode's
  naming: lowerCamelCase, stripped `Image` suffix, lowered leading acronym, `_` before
  a leading digit, and the last namespace component alone.

Matching is case-sensitive and global: a match anywhere in the scanned sources keeps
the asset, whatever the surrounding code does. `.xcassets` directories and
`excluded_dirs` are never scanned as sources. Files are read as UTF-8 with undecodable
bytes dropped.

When you change detection, a missed reference (false "unused") is a release-blocking
bug, while an extra match (false "used") is acceptable. Never narrow a rule to cut
false "used" results unless you can show no real reference style is lost. Every change
needs tests in `tests/test_references.py`. If the reference style is realistic, also
add it to `examples/SampleApp` (and update the smoke-test counts).

## Removal safety rules (enforced, do not weaken)

1. `clean` is a **dry run by default**. Only `--apply` deletes.
2. `--apply` requires `worktree_state(catalogs) == CLEAN` (no uncommitted git changes
   in the catalogs) unless `--force` is passed. "Not a repo" and "git missing" count as
   unknown and are refused with exit `2`.
3. Only whole `.imageset` directories **inside an audited catalog** are deleted
   (`_is_inside_any`). Symlinked image sets are unlinked; their targets are never
   touched. Individual files and `Contents.json` are never edited.
4. Duplicates are **never** auto-removed. Removing one safely means rewriting source
   references, which a pre-build CI step must not do silently.
5. Ignored assets (`ignore` / `--ignore`) are never reported as unused or removed.
6. After `--apply`, scan again so the final report and `--fail-on` reflect what remains.
7. `apply_removal` is the only function that deletes. `plan_removal` only reads (the
   report, plus directory sizes on disk).

## JSON report (public contract)

CI scripts and `action.yml` parse this shape. **Only add keys.** Renaming or removing
one needs a version bump and an update to `action.yml`.

```text
xcassets_path          first catalog; kept for pre-0.2 consumers
xcassets_paths[]       source_path       total_assets
summary.{total_assets, used_assets_count, unused_assets_count, ignored_assets_count,
         total_issues, total_duplicate_images, total_wasted_space_bytes}
issues[].{asset_name, issue_type, detail, path}
duplicate_images[].{sha256, file_size_bytes, files[], wasted_space_bytes}
clean.{applied, count, reclaimed_bytes, imagesets[].{asset_name, path, size_bytes}}   # `clean` command only
```

`IssueType` values are part of the contract too: `action.yml` counts
`issue_type == "duplicate_asset_name"`, and it reads `summary.unused_assets_count`,
`summary.total_duplicate_images`, and `clean.count`. The shape is built in
`AuditReport.to_dict` and `RemovalPlan.to_dict`, and pinned by `tests/test_models.py`.

JSON paths are exactly as resolved from the CLI arguments. Text and Markdown paths are
shown relative to the source directory (`reporting.display_path`).

## Exit codes

Constants in `cli.py`. Do not change their meaning.

| Code | Constant | When |
| --- | --- | --- |
| `0` | `EXIT_SUCCESS` | No `--fail-on` category has findings. Findings without `--fail-on` still exit `0`. |
| `1` | `EXIT_ISSUES_FOUND` | A `--fail-on` category has findings |
| `2` | `EXIT_USAGE_ERROR` | Bad path, no catalog found, bad config, unknown `--fail-on` value, git safety refusal |
| `3` | `EXIT_RUNTIME_ERROR` | `OSError` while scanning, deleting, or writing a report file |
| `77` | `EXIT_PERMISSION_DENIED` | `PermissionError` while scanning |
| `130` | `EXIT_INTERRUPTED` | Ctrl-C |

Output files are written before the exit code is decided, so a failing run still
leaves `--report-json` and `--summary-file` behind. `--summary-file` **appends**
(it targets `$GITHUB_STEP_SUMMARY`); `--report-json` overwrites and creates parent
directories.

## Dependencies

| Kind | Packages | Notes |
| --- | --- | --- |
| Runtime | `typer`, `rich` | Nothing else. TOML, JSON, hashing, globbing, and git access use the standard library (`tomllib`, `json`, `hashlib`, `fnmatch`, `subprocess`). |
| Dev (`dev` group) | `pytest`, `pytest-cov`, `ruff`, `ty` | |
| Build | `uv_build` | `src/` layout, entry point `asset_guard.cli:main` |

- The GitHub Action runs `uvx --from <action path> asset-guard`, so every runtime
  dependency is installed on every CI run of every user. Prefer the standard library,
  and ask before adding a package.
- CI installs with `uv sync --all-groups --locked`. After any change to
  `pyproject.toml` dependencies or `version`, run `uv lock` and commit `uv.lock`.
- The code targets Python 3.12 features on purpose: `Path.walk()`,
  `hashlib.file_digest`, `tomllib`, `StrEnum`. Do not add fallbacks for older versions.
- `git` is an optional external tool, used only by `cleaner.worktree_state`.

## Conventions

- **Ruff `select = ["ALL"]`** (minus docstring rules), line length 100, and `ty` with
  warnings treated as errors. Fix the cause rather than adding `noqa`. If a suppression
  is unavoidable, scope it to one rule code and give a reason. Rules that most often
  catch new code:
  - build an exception message in a `msg` variable first, then `raise X(msg)`;
  - no `print`; use the Rich consoles or `typer.echo`;
  - boolean parameters must be keyword-only;
  - `subprocess` calls need a fixed argv and no shell.
- `ty` checks `src/` only. Ruff checks the tests too, with relaxed rules (asserts,
  magic values, and missing annotations are allowed).
- Annotate every public function. Use builtin generics (`list[str]`, `X | None`).
  Make helper parameters keyword-only (`*,`).
- **All shared data models live in `models.py`.** They are `@dataclass(frozen=True)`
  value objects with no I/O (`AuditReport` is mutable only for test convenience).
  Exceptions: module-private helpers (`catalog.ParsedImageset`,
  `references.References`, `cleaner.WorktreeState`, `cli._Context`) and configuration
  (`config.ScannerConfig`).
- A new `IssueType` member falls into `FindingCategory.BROKEN` unless you map it in
  `IssueType.category`. Decide the category on purpose and extend the parametrized
  test in `tests/test_models.py`.
- Use `pathlib.Path` everywhere. Read text with `encoding="utf-8"`. Walk project and
  source trees with `Path.walk()` and prune `excluded_dirs`. `rglob` is acceptable only
  inside a single catalog or image set.
- Output must be deterministic: sort file lists, issues, groups, and plan entries.
- **stdout carries only the report.** Diagnostics, `-v` output, and errors go to
  stderr. Colors turn off for `--no-color`, `NO_COLOR`, and non-TTY output, by setting
  `color_system=None` (Rich's own `no_color` still leaks bold codes under
  `FORCE_COLOR`). Non-TTY output uses a 160-column width so CI logs do not wrap paths,
  unless `COLUMNS` is set.
- Write user-facing errors with `_fail(console, message, solutions, code)` or
  `_print_error`. Every error names at least one way to fix it.

## Testing

- pytest with `tmp_path`. No real Xcode projects, and no network.
- Test files do not map one-to-one to modules:

  | Test file | Covers |
  | --- | --- |
  | `test_scanner.py` | `scanner.py`, `catalog.py`, `duplicates.py` (helper `_create_imageset`) |
  | `test_references.py` | `references.py` (helper `_write`, fixture `sources`) |
  | `test_cleaner.py` | `cleaner.py`, including real `git` repos built in `tmp_path` |
  | `test_cli.py` | `cli.py` end to end, plus `reporting.format_bytes` (helper `_add_imageset`, fixture `xcode_project`) |
  | `test_reporting.py` | Markdown summary and `display_path` |
  | `test_models.py` | models and the JSON contract |
  | `test_config.py` | config loading and validation |

- Test the CLI with `typer.testing.CliRunner` against `cli.app`. Stub the scan with
  `monkeypatch.setattr(cli, "run_audit", lambda *_args: report)` (helper `_stub_scan`),
  and the git check with `monkeypatch.setattr(cli, "worktree_state", ...)`.
- Strip ANSI codes before asserting on `--help` output (helper `_help_text`): Typer
  forces color when `GITHUB_ACTIONS` or `FORCE_COLOR` is set.
- Every new pattern or check needs both a positive and a negative test. For detection
  changes, the negative test proves an unrelated asset is still reported unused.
- The action is exercised end to end in `.github/workflows/ci.yml` (`action-smoke`, on
  Ubuntu and macOS) against `examples/SampleApp`. Its expected result is 9 assets:
  7 used, 1 ignored (`seasonal_xmas`), 1 unused (`unused_promo`, the only one `clean`
  removes), and 1 duplicate group (`hero_banner` and `hero_banner_copy`). The job lists
  the image sets that must survive by name, so update it when you change the sample.

## Change checklists

| When you change… | Also update |
| --- | --- |
| Reference detection | `tests/test_references.py`; `examples/SampleApp` and the `action-smoke` counts if you add a sample; the detection summary in `README.md` and `docs/ci.md` |
| A CLI option | the shared `Annotated` alias in `cli.py`, both commands, the help tests in `tests/test_cli.py`, `docs/ci.md`, the list in [Commands](#commands) |
| A config key | `CONFIG_KEYS`, `ScannerConfig`, `load_scanner_config`, `tests/test_config.py`, `README.md`, `docs/ci.md`, `examples/SampleApp/.asset-guard.toml` if it is useful there |
| An `IssueType` | `IssueType.category`, `tests/test_models.py`, the feature lists in `README.md` and `docs/ci.md` |
| The JSON shape | `to_dict` in `models.py`, `tests/test_models.py`, the Python snippet in `action.yml`, the [contract](#json-report-public-contract) above |
| Action inputs/outputs | `action.yml`, the `action-smoke` job, the table in `docs/ci.md` |
| `examples/SampleApp` | the `Verify results` step in `.github/workflows/ci.yml` |
| The version (release) | `version` in `pyproject.toml`, then `uv lock`; the pinned `@vX.Y.Z` and `==X.Y.Z` examples in `README.md` and `docs/ci.md` |

Releasing: publishing a GitHub Release tagged `vX.Y.Z` triggers `publish.yml`, which
fails unless the tag equals `v` + the `pyproject.toml` version, then runs lint and
tests, builds, smoke-tests the wheel, and publishes to PyPI. The same tag is what
`uses: nguyen-ta-cuong/asset-guard@vX.Y.Z` runs, straight from source. Do not create
tags or releases unless asked.

## Known gaps

Detection gaps that can cause a **false "unused"** (the dangerous direction):

- **Tokenizer desync.** Strings and comments are tokenized with one regex
  (`_CODE_TOKEN`) that only knows `"…"`, `//`, and `/* */`. An odd number of `"`
  characters inside a Swift multi-line (`"""`) string, or an Objective-C/C char literal
  `'"'`, shifts the tokenizer by one quote, and every later literal in **that file** is
  missed. This is a real bug, not a design choice. Fix it in `references._scan_code`
  with tests if you touch that code.
- **Multi-line string content** is skipped (literals containing a newline are ignored).
- **Names computed at runtime** (server-driven, concatenated with `+`, built with
  `String(format:)` from non-literal parts, case-converted) cannot be detected. Users
  must add them to `ignore`. This is documented in `docs/ci.md`.
- **Other file types** are not scanned: JSON, YAML, `.strings`, `.xcconfig`,
  `project.pbxproj`. Nothing outside the `--source` directory is scanned either.

Gaps that are harmless or only limit features:

- Interpolations that contain quotes (`"\(d["k"])"`) or raw strings with `\#(` may be
  mis-tokenized into extra "used" matches.
- `.colorset`, `.symbolset`, `.dataset`, and app icons are not scanned (only
  `.imageset`).
- Duplicate detection is byte-exact and per file. It does not find re-exported images
  that look identical but differ in bytes, and it does not group at the asset level
  (all scales of set A == set B).
- `worktree_state` runs one `git` command from the first catalog's parent directory.
  Catalogs that live in different repositories come back as unknown.

## Roadmap

1. Asset-level duplicate groups, plus an opt-in `dedupe` command that rewrites string
   references to a canonical asset (local use only, never in CI).
2. More output formats: SARIF (GitHub code scanning) and JUnit XML (Jenkins test UI).
3. `.colorset` / `.symbolset` support.

## Git

Work on feature branches. `main` is the PR base. Use conventional commits
(`feat(cli): …`, `fix(references): …`, `docs: …`). Never commit `.venv`, caches,
`.coverage`, or `docs/private/`. In workflows, keep actions pinned to a full commit SHA
with the version in a trailing comment, as the existing ones are.
