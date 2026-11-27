# asset-guard

A Python (3.12+) CLI that keeps Xcode asset catalogs (`.xcassets`) lean. It finds
**unused**, **duplicate**, and **broken** image assets, and can **remove unused image sets
before you build**. It is designed to run in GitHub Actions, Jenkins, or any other CI.

---

## Features

- **Unused assets:** finds image sets that nothing references. Detection is
  conservative, so `clean` does not delete images that are in use. It checks:
  - every string literal in Swift and Objective-C (`UIImage(named:)`, `Image("…")`,
    enums, constants, SwiftGen, R.swift),
  - dynamic names (`"icon_\(i)"`, `@"icon_%d"`),
  - Xcode 15+ generated symbols (`Image(.logo)`, `UIImage.logo`, `ImageResource.logo`),
  - storyboard/XIB attributes and plist strings.
- **Remove unused assets:** `asset-guard clean` shows what it would delete; `--apply`
  deletes whole `.imageset` folders. It refuses to run when the catalogs have
  uncommitted git changes, so every deletion can be reverted.
- **Duplicate images:** finds byte-identical files across image sets and reports the
  wasted space. Duplicates are reported, never auto-deleted.
- **Broken image sets:** invalid or missing `Contents.json`, missing files, missing
  `@2x`/`@3x`, vectors with an explicit scale, and duplicate asset names.
- **Built for CI:** `--fail-on unused,duplicates,broken`, `--report-json`,
  `--summary-file` (GitHub step summary), meaningful exit codes, a GitHub Action, and a
  Jenkins example. See **[docs/ci.md](docs/ci.md)**.
- **Multiple catalogs:** discovers every `.xcassets` in the project (app, widgets,
  local packages) and skips `Pods`, `.build`, `DerivedData`, and `Carthage`.

---

## Quick start

```bash
# Report only (never modifies files)
uvx --from git+https://github.com/nguyen-ta-cuong/asset-guard asset-guard audit /path/to/MyApp

# Dry run: list unused image sets that would be removed
asset-guard clean /path/to/MyApp

# Delete them (requires committed, clean catalogs, or pass --force)
asset-guard clean /path/to/MyApp --apply

# CI gate: exit 1 if unused or duplicate images exist, and save a JSON report
asset-guard audit . --fail-on unused,duplicates --report-json asset-guard.json
```

Try it on the bundled sample project: `uv run asset-guard audit examples/SampleApp`.

### Configuration (optional)

`.asset-guard.toml` in the project root:

```toml
ignore = ["seasonal_*"]        # asset-name globs that are never reported or removed
exclude-dirs = ["Generated"]   # extra source dirs to skip
expected-scales = ["2x", "3x"] # required bitmap scales
scan-interface-builder = true
```

### CI

```yaml
# GitHub Actions
- uses: nguyen-ta-cuong/asset-guard@main   # pin a tag/SHA
  with:
    command: clean
    apply: "true"
```

See [docs/ci.md](docs/ci.md) for PR checks, release builds, the Jenkins pipeline, exit
codes, and the git safety check.

---

## Development

Requirements: **Python** `>= 3.12` and **[uv](https://docs.astral.sh/uv/)**.

```bash
git clone https://github.com/nguyen-ta-cuong/asset-guard.git
cd asset-guard
make dev      # create .venv with pytest, ruff, ty
make lint     # ruff format check + ruff check + ty
make test     # pytest with coverage gate
uv run asset-guard --help
```

See [AGENTS.md](AGENTS.md) for the module layout, conventions, and safety rules.

---

## Roadmap

1. **Release `v0.2.0`** — Tag the subcommand-based CLI (`audit` / `clean`) so CI workflows
   can pin `asset-guard@v0.2.0` (and optionally publish to PyPI).
2. **Asset-level duplicate grouping & local-only `dedupe`** — Group duplicates where all
   scales of set A match set B, with an opt-in local command that safely rewrites references.
3. **Richer CI report formats** — SARIF (for GitHub Code Scanning annotations on PRs) and
   JUnit XML (for Jenkins test reports), wired through `reporting.py` and `_write_outputs`.
4. **Broader catalog coverage** — Extend `catalog.py` to `.colorset` and `.symbolset`.

---

## License

MIT — see [LICENSE](LICENSE).
