# AGENTS.md

**asset-guard** is a Python CLI that scans Xcode asset catalogs in CI before the app is
built: `audit` reports unused/duplicate/broken image sets, `clean --apply` deletes unused.

## Read this first

- **Never report a used asset as unused.** `clean --apply` deletes whatever is reported
  unused, and a missing image is a runtime bug the compiler does not catch. A false
  "used" only keeps a few kilobytes. When in doubt, count the asset as used.
- **Done means** `make lint && make test` pass, new behavior has a positive and a negative
  test, and the files in [Change checklist](#change-checklist) are updated.
- **Do not weaken** the removal safety rules, the JSON contract, or the exit codes.
- **Ask before adding a runtime dependency.** Runtime is `typer` + `rich` only, because
  the GitHub Action installs from source on every user's CI run.

## Commands

- Toolchain is `uv`, Python ≥ 3.12. `make dev` installs, `make format` formats,
  `make lint` runs `ruff format --check`, `ruff check`, `ty check src/`, and `make test`
  runs pytest with a branch coverage gate (≥ 80%, currently ~99%).
- Some tests: `uv run pytest tests/test_references.py -k raw --no-cov`. Without `--no-cov`
  the coverage gate reports a failure although the tests pass.
- Run from source: `uv run asset-guard audit examples/SampleApp`. After changing
  dependencies or `version`, run `uv lock` (CI installs with `--locked`).

## Layout

```text
src/asset_guard/
├── cli.py         # Typer app (audit, clean): paths, config, git safety, outputs, exit codes
├── config.py      # ScannerConfig, .asset-guard.toml loading, ignore globs
├── models.py      # all shared dataclasses + the JSON shape (pure, no I/O)
├── catalog.py     # discover catalogs, find .imageset dirs, resolve names, validate
├── references.py  # Swift/ObjC strings, dynamic patterns, Swift symbols, IB, plists
├── duplicates.py  # size pre-filter + SHA-256 grouping
├── scanner.py     # AssetScanner: catalogs + references + duplicates -> AuditReport
├── cleaner.py     # plan_removal, apply_removal (the ONLY place that deletes), git check
└── reporting.py   # Rich text and Markdown summary
action.yml  .github/workflows/{ci,publish}.yml  examples/SampleApp/  docs/ci.md
```

Flow: `cli._prepare → _scan → AssetScanner.scan → [clean: plan_removal → worktree_state →
apply_removal → _scan again] → render → _exit_for_findings`. Imports go one way:
`cli → {scanner, cleaner, reporting} → {catalog, references, duplicates} → {config, models}`.

## Business rules

- **`--fail-on` categories:** `unused`; `duplicates` = identical files **plus**
  `duplicate_asset_name`; `broken` = every other `IssueType`. A new `IssueType` lands in
  `broken` unless mapped in `IssueType.category`.
- **Classification order:** referenced → used; else matches an ignore glob → ignored; else
  unused. Broken image sets are classified too, so `clean` can delete a broken unused one.
- **Paths:** `PROJECT` defaults to the cwd; sources default to `PROJECT`; catalogs default
  to every `*.xcassets` under it (none found → exit `2`).
- **Config:** auto-loaded only from `PROJECT/.asset-guard.toml` (no upward search). Keys:
  `ignore`, `exclude-dirs`, `expected-scales`, `scan-interface-builder`; unknown keys are
  errors. `exclude-dirs` adds to the defaults and matches directory names at any depth,
  for both catalog discovery and the source scan.
- **Asset name:** the `.imageset` folder name, prefixed with parent folders only when
  they set `provides-namespace`. Ignore globs match that full name (`fnmatchcase`).
  Duplicate names are checked per catalog; the same name in two catalogs is fine.
- **`clean`:** `applied` is true only with `--apply` **and** a non-empty plan (otherwise
  the git check is skipped). After deleting, the re-scan makes `summary` and `--fail-on`
  describe what remains; the `clean` section describes what was removed.

## Reference detection: `References.uses(name)` is True if the asset *might* be used

- **Exact name:** any string literal in `.swift/.m/.mm/.h` (comments stripped), each line
  of a Swift multi-line string, any IB `="value"` attribute, any plist `<string>`.
  A namespaced `onboarding/hero` also matches by bare name (`"hero"`, patterns too).
- **Dynamic pattern:** interpolation or printf specifiers become a full-match regex
  (`"icon_\(i)"`, `#"icon_\#(i)"#`, `@"icon_%d"` → `icon_.*`). Needs ≥ 2 alphanumeric
  literal characters, otherwise `"\(a)_\(b)"` would match everything.
- **Swift symbol** (`.swift` only): any `swift_symbol_candidates(name)` in a member chain:
  every member of an implicit chain (`.logo`, `.Onboarding.hero`), or the members after
  the first capitalised name (`ImageResource.Onboarding.hero`, not `view.icon`).

Matching is case-sensitive and global: one match anywhere keeps the asset. The regex
tokenizers (`_SWIFT_TOKEN`, `_C_TOKEN`) know plain, raw, and multi-line strings, comments,
and C char literals; an unbalanced quote costs one line, never the rest of the file.
A missed reference (false "unused") is a release-blocking bug; an extra match is
acceptable. Never narrow a rule unless you can show no real reference style is lost.

## Removal safety rules (enforced, do not weaken)

1. `clean` is a dry run by default. Only `--apply` deletes.
2. `--apply` requires `worktree_state(catalogs) == CLEAN` unless `--force`. "Not a repo"
   and "git missing" are unknown and refused (exit `2`). Only the catalogs are checked.
3. Only whole `.imageset` directories strictly inside an audited catalog are deleted.
   Symlinked sets are unlinked; their targets are never touched.
4. Duplicates are never auto-removed, and ignored assets are never reported or removed.
5. After `--apply`, scan again so the report and `--fail-on` reflect what remains.

## Public contracts

**JSON** (built in `models.py`, pinned by `tests/test_models.py`): only add keys.
Renaming or removing one needs a version bump and an `action.yml` update.

```text
xcassets_path (first catalog, legacy)  xcassets_paths[]  source_path  total_assets
summary.{total_assets, used_assets_count, unused_assets_count, ignored_assets_count,
         total_issues, total_duplicate_images, total_wasted_space_bytes}
issues[].{asset_name, issue_type, detail, path}    duplicate_images[].{sha256,
         file_size_bytes, files[], wasted_space_bytes}
clean.{applied, count, reclaimed_bytes, imagesets[].{asset_name, path, size_bytes}}
```

**Exit codes** (constants in `cli.py`): `0` no `--fail-on` category matched (findings
alone still exit `0`), `1` a `--fail-on` category matched, `2` usage/config error or git
safety refusal, `3` `OSError` while scanning, deleting, or writing a report, `77`
permission denied, `130` interrupted. `--summary-file` appends; `--report-json` overwrites.

## Conventions and testing

- Ruff `select = ["ALL"]` (minus docstrings), line length 100, `ty` warnings are errors
  (`src/` only). Fix the cause instead of adding `noqa`. Common trips: put exception text
  in a `msg` variable before `raise`; no `print`; boolean parameters are keyword-only.
- Shared data models live in `models.py` as frozen dataclasses with no I/O. `models` and
  `config` import nothing from the package.
- `pathlib.Path` everywhere, `encoding="utf-8"`, sorted (deterministic) output. Walk
  project trees with `Path.walk()` and prune `excluded_dirs`; no `rglob` over a project.
- stdout carries only the report; diagnostics and errors go to stderr via `_fail` /
  `_print_error`, and each error names a way to fix it.
- New CLI options are `Annotated` aliases shared by both commands. Action inputs are
  passed via `env:`, never interpolated into `run:`. Workflow actions stay SHA-pinned.
- Git: feature branches, PRs to `main`, conventional commits; never commit `docs/private/`.
- pytest + `tmp_path`, no real Xcode projects. `test_scanner.py` also covers `catalog.py`
  and `duplicates.py`. CLI tests use `CliRunner` on `cli.app` and stub with
  `monkeypatch.setattr(cli, "run_audit", lambda *_args: report)` / `cli.worktree_state`.
- `action-smoke` in `ci.yml` runs the action on `examples/SampleApp` and expects 11 assets:
  9 used, 1 ignored, 1 unused (`unused_promo`, the only one removed), 1 duplicate group.

## Change checklist

| When you change… | Also update |
| --- | --- |
| Reference detection | `tests/test_references.py`, `examples/SampleApp` (+ smoke counts), `README.md`, `docs/ci.md` |
| CLI option or config key | `cli.py` / `config.py` (`CONFIG_KEYS`), their tests, `README.md`, `docs/ci.md` |
| `IssueType` | `IssueType.category`, `tests/test_models.py`, `README.md`, `docs/ci.md` |
| JSON shape | `to_dict`, `tests/test_models.py`, the Python snippet in `action.yml` |
| Action inputs/outputs | `action.yml`, the `action-smoke` job, `docs/ci.md` |
| Version | `pyproject.toml`, `uv lock`, pinned `@vX.Y.Z` / `==X.Y.Z` in `README.md`, `docs/ci.md` (release tag must be `v` + version; never tag or release unless asked) |

## Known gaps

- Names computed at runtime (server-driven, `+` concatenation, case conversion) cannot be
  detected; users add them to `ignore`. JSON, YAML, `.strings`, and `project.pbxproj`
  are not scanned, nor is anything outside `--source`.
- Only `.imageset` is scanned. Duplicate detection is byte-exact and per file, not per asset.
