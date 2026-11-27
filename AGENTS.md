# AGENTS.md

Guidance for AI coding agents (and humans) working on **asset-guard**.

## Mission

`asset-guard` is a Python CLI for iOS developers. It scans Xcode asset catalogs
(`.xcassets`) and runs in CI (GitHub Actions, Jenkins, …) **before the app is built**
to:

1. **Remove unused image assets**: image sets that nothing in the project references
   (`asset-guard clean --apply`).
2. **Surface duplicate image assets**: byte-identical files. They are reported and
   gated with `--fail-on duplicates`, **never auto-deleted** (see the safety rules).

It also reports broken image sets (bad `Contents.json`, missing files, missing
`@2x`/`@3x`, vectors with an explicit scale, duplicate names).

The most important property is **never deleting an asset that is still used**. A
missing image is a runtime bug that the compiler does not catch. Every design decision
below follows from this.

## Commands

The toolchain is `uv`, Python ≥ 3.12. Use the Makefile targets:

| Task | Command |
| --- | --- |
| Install all deps | `make dev` (`uv sync --all-groups`) |
| Lint + type-check | `make lint` (ruff format check, ruff check, `ty check src/`) |
| Auto-format | `make format` |
| Tests + coverage | `make test` (pytest, coverage gate **≥ 80%**, currently ~99%) |
| Report only | `uv run asset-guard audit [PROJECT] [--fail-on unused,duplicates,broken,any]` |
| Remove unused | `uv run asset-guard clean [PROJECT] [--apply] [--force]` |
| Demo project | `uv run asset-guard audit examples/SampleApp` |

Shared options: `--xcassets P` (repeatable), `--source P`, `--config P`,
`--ignore GLOB` (repeatable), `--format text|json`, `--report-json P`,
`--summary-file P`, `--no-color`, `-v`.

Before you call a change done, run `make lint && make test`. Both must pass.

## Layout and data flow

```text
src/asset_guard/
├── cli.py          # Typer app (audit, clean): path/config resolution, git safety, outputs, exit codes
├── config.py       # ScannerConfig, defaults, .asset-guard.toml loading (tomllib), ignore globs
├── models.py       # All data models: IssueType, FindingCategory, AssetIssue, DuplicateImage, AuditReport,
│                   #   RemovalCandidate, RemovalPlan (pure, no I/O)
├── catalog.py      # .xcassets side: discover catalogs, find .imageset dirs, resolve names, validate
├── references.py   # Source side: Swift/ObjC strings, dynamic patterns, Swift symbols, IB attributes, plists
├── duplicates.py   # Size pre-filter + SHA-256 grouping of identical files
├── scanner.py      # AssetScanner orchestrator: catalogs + references + duplicates -> AuditReport
├── cleaner.py      # plan_removal (pure), apply_removal (the ONLY place that deletes), git worktree check
└── reporting.py    # Rich text (audit/clean) and Markdown summary rendering
tests/              # one test module per source module (tmp_path fixtures, no real Xcode projects)
action.yml          # composite GitHub Action (setup-uv + uvx --from action path)
.github/workflows/ci.yml   # lint/test + action smoke test against examples/SampleApp
examples/SampleApp/        # tiny fake Xcode project covering every reference style
docs/ci.md                 # user docs: GitHub Actions, Jenkins, exit codes, safety check
docs/swift-to-python.md    # learning notes for the Swift-background maintainer
```

```text
cli.audit / cli.clean ─▶ _prepare (paths, config, --fail-on) ─▶ run_audit ─▶ AssetScanner.scan
                                                                    ├─ catalog.find_imagesets / parse_imageset (per catalog)
                                                                    ├─ references.collect_used_references → References.uses(name)
                                                                    └─ duplicates.find_duplicate_images
   clean only: plan_removal(report) ─▶ worktree_state ─▶ apply_removal ─▶ re-scan
   output: reporting.* to stdout, JSON to stdout / --report-json, Markdown to --summary-file
```

Dependency direction: `cli → {scanner, cleaner, reporting} → {catalog, references,
duplicates} → {config, models}`. `models` and `config` import nothing from the
package. Keep it that way, with no cycles.

### Where new work goes

| Feature | Module |
| --- | --- |
| New reference syntax / file type | `references.py` (keep it conservative, see below) |
| New catalog check (idioms, colorsets…) | `catalog.py`, new `IssueType` member (+ `category`) |
| New config key | `config.py` (`CONFIG_KEYS`, validation, `load_scanner_config`) and `docs/ci.md` |
| New removal behavior | `cleaner.py` (planning stays pure; deletion only in `apply_removal`) |
| New CLI option | `cli.py`: define it once as an `Annotated` alias and share it between commands |
| New output format (SARIF, JUnit…) | `reporting.py`, wired through `_write_outputs` |
| New action input/output | `action.yml`, pass it via `env:` (never interpolate `${{ }}` into `run:`), plus the smoke test |

## Reference detection is conservative, by design

`References.uses(name)` returns True if the asset **might** be used:

- an exact string literal anywhere in `.swift/.m/.mm/.h` (comments are stripped), or
  any IB attribute value, or any plist `<string>`;
- a dynamic pattern built from interpolation or format strings (`"icon_\(i)"` →
  `icon_.*`). A pattern needs ≥ 2 alphanumeric literal characters, otherwise
  `"\(a)_\(b)"` would match everything;
- any `swift_symbol_candidates(name)` appearing as an implicit member (`.logo`) or as
  `ImageResource|UIImage|NSImage|Image.logo`. Candidates over-approximate Xcode's
  naming (stripped `Image` suffix, lowered acronyms, `_` before a leading digit, last
  namespace component).

When you change detection, a missed reference (false "unused") is a release-blocking
bug, while an extra match (false "used") is acceptable. Every fix needs a test in
`tests/test_references.py`. If the reference style is realistic, also add it to
`examples/SampleApp`.

## Conventions

- **Ruff `select = ["ALL"]`**, line length 100, and `ty` with warnings treated as
  errors. Fix the cause rather than adding `noqa`. If a suppression is unavoidable,
  scope it to one rule code and give a reason.
- Annotate every public function. Use builtin generics (`list[str]`, `X | None`).
  Make helper parameters keyword-only (`*,`).
- **All shared data models live in `models.py`** (report, issues, duplicates, removal
  plan). They are `@dataclass(frozen=True)` value objects with no I/O (`AuditReport`
  is mutable only for test convenience). Modules hold behavior; `models.py` holds data.
  Exceptions: module-private helpers (`catalog.ParsedImageset`,
  `references.References`, `cleaner.WorktreeState`) and configuration
  (`config.ScannerConfig`).
- **The JSON shape is a public contract** that CI scripts and `action.yml` parse:
  `summary.*`, `issues[]`, `duplicate_images[]`, `clean.{applied,count,reclaimed_bytes,imagesets}`.
  Only add keys. Renaming or removing one needs a version bump and an update to
  `action.yml`. (`xcassets_path` is kept as the first catalog for pre-0.2 consumers.)
- Use `pathlib.Path` everywhere. Read text with `encoding="utf-8"`. Walk directories
  with `Path.walk()` and prune `excluded_dirs`, never with `rglob` over a whole project.
- Output must be deterministic: sort file lists, issues, groups, and plan entries.
- **stdout carries only the report.** Diagnostics, `-v` output, and errors go to stderr.
  Colors turn off for non-TTY output and `NO_COLOR`. Non-TTY output uses a 160-column
  width so CI logs do not wrap paths.
- Exit codes are constants in `cli.py`: `0` OK, `1` `--fail-on` matched, `2` usage/config
  error or git safety refusal, `3` runtime error, `77` permission denied, `130`
  interrupted. CI users depend on them, so don't change their meaning.
- Write user-facing errors with `_print_error` / `_fail(console, message, solutions, code)`.

## Testing

- pytest with the `tmp_path` fixture. Build fake catalogs on disk with
  `_create_imageset` (scanner tests) or `_add_imageset` (CLI tests).
- Test the CLI with `typer.testing.CliRunner`. Stub the scan with
  `monkeypatch.setattr(cli, "run_audit", lambda *_args: report)`, and stub the git check
  with `monkeypatch.setattr(cli, "worktree_state", ...)`.
- Every new pattern or check needs both a positive and a negative test.
- The action is exercised end to end in `.github/workflows/ci.yml` (`action-smoke`)
  against `examples/SampleApp`. Keep its expected counts in sync if you change the
  sample.

## Safety rules for removal (enforced, don't weaken)

1. `clean` is a **dry run by default**. Only `--apply` deletes.
2. `--apply` requires `worktree_state(catalogs) == CLEAN` (no uncommitted git changes in
   the catalogs) unless `--force` is passed. "Not a repo" or "git missing" counts as
   unknown and is refused.
3. Only whole `.imageset` directories that are **inside an audited catalog** are
   deleted (`_is_inside_any`). Symlinked image sets are unlinked; their targets are
   never touched.
4. Duplicates are **never** auto-removed. Removing one safely means rewriting source
   references, which a pre-build CI step must not do silently.
5. Ignored assets (`ignore` / `--ignore`) are never reported as unused or removed.
6. After `--apply`, re-scan so the final report and `--fail-on` reflect what remains.

## Known gaps

- `.colorset`, `.symbolset`, `.dataset`, and app icons are not scanned (only `.imageset`).
- Names that are fully computed at runtime (server-driven, concatenated with `+`) cannot
  be detected. Users must add them to `ignore`. This is documented in `docs/ci.md`.
- Duplicate detection is byte-exact and per file. It does not find re-exported images
  that look identical but differ in bytes, and it does not group at the asset level
  (all scales of set A == set B).
- Swift string tokenizing is regex-based. Interpolations that contain quotes
  (`"\(d["k"])"`) or raw strings with `\#(` may be mis-tokenized. The failure is
  usually harmless (an extra "used" match).
- Reported paths in text/Markdown are relative to the source dir. JSON paths are
  exactly as resolved from the CLI arguments.

## Roadmap

1. Release `v0.2.0` (subcommands are a breaking CLI change from 0.1) and tag it, so
   `uses: …/asset-guard@v0.2.0` and `uvx --from git+…@v0.2.0` can be pinned. Consider
   publishing to PyPI.
2. Asset-level duplicate groups, plus an opt-in `dedupe` command that rewrites string
   references to a canonical asset (local use only, never in CI).
3. More output formats: SARIF (GitHub code scanning) and JUnit XML (Jenkins test UI).
4. `.colorset` / `.symbolset` support.

## Git

Work on feature branches. `main` is the PR base. Use conventional commits
(`feat(cli): …`, `fix(references): …`, `docs: …`). Never commit `.venv`, caches, or
`.coverage`.
