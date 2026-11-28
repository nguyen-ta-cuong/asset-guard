# Using asset-guard in CI (GitHub Actions & Jenkins)

asset-guard runs non-interactively, writes machine-readable reports, and uses exit
codes CI can act on. There are two ways to use it, and most teams want both:

| Where | Command | Purpose |
| --- | --- | --- |
| **Pull requests** | `asset-guard audit . --fail-on unused,duplicates` | Block unused or duplicate images before they are merged, so the repo stays clean. Runs on Linux, no Xcode needed. |
| **Release / build jobs** | `asset-guard clean . --apply` then `xcodebuild …` | Remove any unused image sets that remain from the build checkout, so they never ship in the app. |

> [!IMPORTANT]
> `clean --apply` deletes files **only in the CI checkout**. Don't commit after it. If a
> later step runs `git add -A && git commit` (e.g. a fastlane version bump), it will
> commit the deletions too. Put such steps before asset-guard, or stage files explicitly.

## What `clean` removes, and what it never removes

- **Removed:** whole `.imageset` folders whose names are not referenced anywhere in
  Swift, Objective-C, storyboards, XIBs, or plists.
- **Never removed:** duplicate images. A duplicate whose name is still referenced would
  turn into a missing image at runtime. Use `--fail-on duplicates` to make developers
  merge them in source instead.
- **Never removed:** assets matching an `ignore` glob in `.asset-guard.toml` or
  `--ignore`.

Detection is conservative. An asset counts as used if its name appears in **any**
string literal, matches a dynamic name like `"icon_\(i)"` or `@"icon_%d"`, or matches a
generated Swift symbol (`Image(.icon)`, `UIImage.icon`, `ImageResource.icon`). The
result can be an unused asset kept by mistake (harmless), but should not be a used asset
deleted.

Folders with **Provides Namespace** ticked are resolved: an image set `hero` inside
`onboarding` is the asset `onboarding/hero`. It counts as used when the code:

- says `Image("onboarding/hero")`;
- uses a generated symbol, nested (`Image(.Onboarding.hero)`,
  `ImageResource.Onboarding.hero`) or flattened (`Image(.onboardingHero)`);
- contains the end of the name as a string or dynamic name (`"hero"`, `"/hero"`,
  `"/hero_\(i)"`), because the folder prefix may be added with `+`.

No ignore glob is needed for these. Folders without the tick do not change the name.
A symbol counts after any capitalised name (`Asset.hero`, `Theme.hero`), so an asset
named like a common static member (`red`, `zero`, `shared`) is rarely reported unused.

Assets the scanner cannot see go in the ignore list. Examples are names loaded from a
server or remote config, or names built in ways other than interpolation or format
strings:

```toml
# .asset-guard.toml (project root)
ignore = ["seasonal_*", "promo_banner_*"]
exclude-dirs = ["Generated"]   # extra source dirs to skip
```

Globs match the full asset name, so a namespaced asset needs its folder:
`"onboarding/*"` matches `onboarding/hero`, and `"hero"` does not.

## Exit codes

| Code | Meaning | Suggested CI handling |
| --- | --- | --- |
| `0` | Success (nothing matched `--fail-on`) | pass |
| `1` | Findings in a `--fail-on` category | fail the PR, or mark the build unstable |
| `2` | Usage/config error, or `clean --apply` refused by the git safety check | fail |
| `3` | Runtime error (filesystem, unwritable report) | fail |
| `77` | Permission denied | fail |

`--fail-on` takes a comma-separated list of `unused`, `duplicates` (identical images and
duplicate names), `broken` (bad `Contents.json`, missing files or scales), and `any`.

## Outputs for CI

- `--report-json PATH`: full JSON report (with a `clean` section for `clean`). Archive
  it as a build artifact.
- `--summary-file PATH`: appends a Markdown summary. Point it at `$GITHUB_STEP_SUMMARY`
  on GitHub. On Jenkins, archive it or publish it.
- Logs are plain text: colors turn off automatically when output is not a terminal,
  and `NO_COLOR` is respected.

## Git safety check

`clean --apply` refuses to delete anything (exit `2`) unless the asset catalogs have
**no uncommitted changes**, so every deletion can be undone with `git checkout`. A fresh
CI checkout passes this check. If it fails in CI:

- A workspace was reused and still has deletions from an earlier run. Use a fresh or
  clean checkout (Jenkins: *Wipe out repository* / `cleanWs()`, or
  `checkout scm`, which restores tracked files).
- git refuses to run in a container because of "dubious ownership". Run
  `git config --global --add safe.directory "$PWD"`.
- You accept the risk: pass `--force`.

---

## GitHub Actions

This repository ships a composite action (`action.yml`). It installs `uv`, runs
asset-guard from the action's own source, writes the step summary, and exposes outputs.

### PR check (Linux runner, cheap)

```yaml
# .github/workflows/assets.yml
name: Assets
on: pull_request
permissions:
  contents: read
jobs:
  asset-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: nguyen-ta-cuong/asset-guard@v0.1.0 # pin a release tag or commit SHA
        with:
          command: audit
          fail-on: unused,duplicates
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: asset-guard-report
          path: asset-guard-report.json
```

### Strip unused images before building (macOS runner)

```yaml
jobs:
  build:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v7

      - name: Remove unused image assets
        id: assets
        uses: nguyen-ta-cuong/asset-guard@v0.1.0
        with:
          command: clean
          apply: "true"
          fail-on: broken            # optional: stop on broken image sets

      - run: echo "Removed ${{ steps.assets.outputs.removed-count }} unused image sets"

      - name: Build
        run: xcodebuild -scheme MyApp -configuration Release build
```

### Action inputs and outputs

| Input | Default | Description |
| --- | --- | --- |
| `command` | `audit` | `audit` or `clean` |
| `path` | `.` | Project root |
| `apply` | `false` | `clean` only: really delete |
| `force` | `false` | `clean` only: skip the git safety check |
| `fail-on` | | e.g. `unused,duplicates` |
| `config` | | Path to `.asset-guard.toml` |
| `report-json` | `asset-guard-report.json` | JSON report path |
| `args` | | Extra CLI args, e.g. `--ignore promo_* --verbose` |

Outputs: `exit-code`, `report-json`, `unused-count`, `duplicate-count`,
`removed-count`.

---

## Jenkins

Install [uv](https://docs.astral.sh/uv/) on the macOS agent once (`brew install uv`).
`uvx` then runs asset-guard in an isolated environment and downloads Python 3.12 if it
is needed.

```groovy
// Jenkinsfile
pipeline {
    agent { label 'macos' }

    environment {
        // Pin a specific PyPI version for reproducible builds.
        ASSET_GUARD = 'asset-guard==0.1.0'
        NO_COLOR = '1'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Asset Guard') {
            steps {
                script {
                    def rc = sh(returnStatus: true, script: '''
                        uvx "$ASSET_GUARD" clean . --apply \
                            --fail-on duplicates,broken \
                            --report-json reports/asset-guard.json \
                            --summary-file reports/asset-guard.md
                    ''')
                    if (rc == 1) {
                        unstable('asset-guard: duplicate or broken image assets found')
                    } else if (rc != 0) {
                        error("asset-guard failed with exit code ${rc}")
                    }
                }
            }
            post {
                always {
                    archiveArtifacts artifacts: 'reports/asset-guard.*', allowEmptyArchive: true
                }
            }
        }

        stage('Build') {
            steps {
                sh 'xcodebuild -scheme MyApp -configuration Release build'
            }
        }
    }
}
```

For a PR job that only reports, swap the command for
`asset-guard audit . --fail-on unused,duplicates` and let exit code `1` fail the
build.

## Any other CI (GitLab, Bitrise, Xcode Cloud, fastlane)

The same two commands work anywhere `uv` (or Python ≥ 3.12 with `pipx`) is available:

```bash
uvx asset-guard==0.1.0 clean . --apply --report-json asset-guard.json
```

fastlane: `sh("cd .. && uvx asset-guard==0.1.0 clean . --apply")` in a lane, before
`build_app`.
