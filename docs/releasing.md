# Releasing

This is the maintainer runbook for cutting a RepoTrials release. It covers the
one-time setup that cannot be automated, the version bump, the tag, what each
workflow does, and how a third party can verify what was published.

RepoTrials has not been released yet. There is no tag, no GitHub Release, and
the `repotrials` name is not claimed on PyPI. Everything below describes the
first release as much as it does the tenth.

## One-time manual setup

These steps require repository-owner or PyPI-account access and must be done by
hand. No workflow can perform them.

### 1. PyPI trusted publisher

Publishing uses OpenID Connect, not a stored API token. PyPI verifies a
short-lived token that GitHub mints for one workflow in one environment.

For a project that does not exist on PyPI yet, add a *pending* publisher at
<https://pypi.org/manage/account/publishing/>:

| Field | Value |
| --- | --- |
| PyPI project name | `repotrials` |
| Owner | `PozziTiv4ik` |
| Repository name | `Repo-Trials` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Repeat at <https://test.pypi.org/manage/account/publishing/> with the
environment name `testpypi` to enable the rehearsal path.

Check that the `repotrials` name is actually available before the first
publish. The first successful upload claims it permanently; PyPI does not
transfer names on request.

If a `PYPI_API_TOKEN` secret exists in this repository, delete it. The release
workflow does not read one, and an unused publishing credential is a liability.

### 2. GitHub environments

Settings → Environments. Create two:

- `pypi` — restrict deployment branches and tags to the tag pattern `v*`, and
  add a required reviewer. This makes publishing to the real index an explicit
  human decision even when a tag push triggers it automatically.
- `testpypi` — no protection needed.

### 3. GitHub Pages source

Settings → Pages → Build and deployment → Source: **GitHub Actions**.

Until this is set once by hand, `docs.yml` fails at the deploy step with a
"Pages is not enabled" error. The workflow cannot set it; it is a repository
setting.

### 4. Branch protection on `main`

Settings → Rules → Rulesets (or Branches → Branch protection rules):

- require a pull request before merging;
- require the `CI` status checks to pass — at minimum the Ubuntu 3.11 and 3.13
  matrix legs and `Build and inspect distributions`;
- require the `CodeQL` analysis to pass;
- require branches to be up to date before merging;
- block force pushes and deletions.

OpenSSF Scorecard reads these settings, so this step is also what moves the
Branch-Protection score off zero.

### 5. Optional: Scorecard token

The default `GITHUB_TOKEN` cannot read branch-protection settings. To score
that check, create a fine-grained personal access token with read-only
`administration` permission on this repository, store it as the
`SCORECARD_TOKEN` secret, and uncomment the `repo_token` line in
`.github/workflows/scorecard.yml`. Leaving it unset is acceptable; the check is
then reported as inconclusive rather than failed.

Do not add a Scorecard badge to `README.md` until a run has published a real
score. The badge URL after the first successful run is
`https://api.securityscorecards.dev/projects/github.com/PozziTiv4ik/Repo-Trials/badge`.

## Version locations

The version is written in four places and they must agree. The release workflow
cross-checks the first two and the tag, and fails the build on a mismatch; the
other two are your responsibility.

| File | Field |
| --- | --- |
| `pyproject.toml` | `[project] version` |
| `src/repotrials/__init__.py` | `__version__` |
| `CITATION.cff` | `version`, and add or update `date-released` |
| `Dockerfile` | `ARG REPOTRIALS_VERSION` |

`repotrials --version` and the `repotrials_version` field recorded in every run
manifest and report both read `src/repotrials/__init__.py`, so a stale value
there silently mislabels stored evaluation results. That is the one mismatch
that corrupts data rather than just documentation.

## Cutting a release

### 1. Prepare the changelog

`CHANGELOG.md` keeps an `## [Unreleased]` section. Rename it to the release
version and open a fresh empty `## [Unreleased]` above it:

```markdown
## [Unreleased]

## [0.2.0] - 2026-08-20
```

The release workflow extracts the section whose heading label matches the
version and uses it as the GitHub Release body, so the heading must be exactly
`## [0.2.0]` or `## [0.2.0] - <date>`. If no matching section is found the
release still publishes, with a body that points at the changelog — an obvious
signal that this step was skipped.

Record CLI, schema, and exit-code changes explicitly. Consumers are told to
check `schema_version` and reject unknown values; a schema bump that is not in
the changelog breaks that contract.

### 2. Bump the version

Edit the four files listed above. Then confirm locally:

```bash
python -m pip install -e ".[dev]"
make check
python scripts/demo.py
repotrials --version
```

### 3. Merge

Open a pull request with the bump and the changelog, let CI pass, and merge to
`main`. Tag a merged commit, never a local one.

### 4. Rehearse (recommended for the first release)

Actions → Release → Run workflow:

- `target: none` — builds, runs the full verification suite, cross-checks the
  version, validates metadata with `twine check --strict`, and installs the
  wheel into a clean virtual environment. Nothing is published and no
  attestation is written.
- `target: testpypi` — the same, then uploads to TestPyPI. Install from there
  in a clean environment before touching the real index:

  ```bash
  python -m pip install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ repotrials
  ```

  The extra index is needed because TestPyPI does not mirror dependencies.
  RepoTrials has no runtime dependencies, so this only matters if that ever
  changes.

TestPyPI is a scratch index. Its history is not authoritative and version
numbers there can be burned freely.

### 5. Tag and push

```bash
git switch main
git pull --ff-only
git tag -a v0.2.0 -m "RepoTrials 0.2.0"
git push origin v0.2.0
```

The tag must be `v` plus the exact packaged version. `v0.2.0` with
`pyproject.toml` at `0.2.1` fails the build job before anything is published.

### 6. Approve the publish

The tag push starts `Release`. When it reaches the `Publish to PyPI` job it
waits on the `pypi` environment's required reviewer. Approve it, and the
workflow publishes to PyPI and then creates the GitHub Release.

### 7. Verify (see below), then announce

## What the workflows do

### `.github/workflows/release.yml`

Triggered by a `v*` tag push, and manually via `workflow_dispatch` with a
`target` input (`none`, `testpypi`, `pypi`).

| Job | Runs when | Does |
| --- | --- | --- |
| `verify` | always | ruff check, ruff format --check, mypy --strict, pytest with the coverage gate, and the end-to-end demo, on the tagged commit |
| `build` | after `verify` | cross-checks tag/`pyproject.toml`/`__version__`, `python -m build`, `twine check --strict`, wheel install smoke test, build provenance attestation, uploads `dist/` as an artifact |
| `publish-testpypi` | dispatch with `target: testpypi` | trusted-publishing upload to TestPyPI |
| `publish-pypi` | tag push, or dispatch with `target: pypi` | trusted-publishing upload to PyPI, gated on the `pypi` environment |
| `github-release` | tag push only | extracts notes from `CHANGELOG.md`, appends SHA-256 digests and verification instructions, creates the Release with the sdist and wheel attached |

`CI` does not run on tags, which is why `release.yml` repeats its checks rather
than assuming a green run on `main`. The tagged tree is what gets published, so
the tagged tree is what gets tested.

A manual dispatch never creates a GitHub Release, because there is no tag to
attach it to.

### `.github/workflows/docs.yml`

Builds the MkDocs Material site with `mkdocs build --strict` on every push to
`main` and deploys it to GitHub Pages. `--strict` means a broken internal link
or an unknown configuration key fails the run instead of publishing a damaged
site. If `requirements-docs.txt` exists it is installed alongside
`mkdocs-material`; that is where extra MkDocs plugins belong.

### `.github/workflows/scorecard.yml`

Runs OpenSSF Scorecard weekly, on pushes to `main`, and when a branch
protection rule changes. Results go to code scanning and, because
`publish_results` is enabled, to the public OpenSSF API — which is what makes
the resulting badge verifiable by someone who does not trust this repository.

## Verifying a published artifact

Anything below can be run by a third party. Publish the commands, not a
reassurance.

### Build provenance

Every artifact attached to a GitHub Release carries a signed SLSA-style
provenance attestation naming the workflow, the repository, and the commit that
produced it:

```bash
gh release download v0.2.0 --repo PozziTiv4ik/Repo-Trials --pattern '*.whl'
gh attestation verify --repo PozziTiv4ik/Repo-Trials repotrials-0.2.0-py3-none-any.whl
```

A wheel downloaded from PyPI has the same digest as the one attached to the
Release, so the same command verifies it.

### Digests

The Release body lists the SHA-256 of every artifact. Compare against what you
downloaded:

```bash
python -m pip download --no-deps --no-binary :all: repotrials==0.2.0 -d /tmp/rt
sha256sum /tmp/rt/*
```

### Installed package

```bash
pipx install repotrials==0.2.0
repotrials --version
repotrials doctor
```

`--version` must print the tagged version. If it prints something else, the
`src/repotrials/__init__.py` bump was missed and the release should be yanked.

### Source distribution contents

The sdist ships the schemas, docs, and `scripts/demo.py`; CI asserts this on
every run, and it is worth re-checking once from the published file:

```bash
tar -tzf repotrials-0.2.0.tar.gz | grep -E 'schemas/|docs/|scripts/demo.py'
```

## If a release is wrong

- **Never re-upload a fixed artifact under the same version.** PyPI rejects it,
  and anyone who already installed the bad one keeps it.
- Yank the release on PyPI (`Manage` → `Yank`). Yanking hides it from new
  resolutions while leaving pinned installs working — the right tool for a
  broken but not dangerous release.
- Mark the GitHub Release as a pre-release or delete it, and say what happened
  in `CHANGELOG.md`.
- Fix forward with a patch version. Do not delete or move the tag: task IDs,
  run manifests, and comparison digests recorded against a revision are meant
  to be resolvable later.
- If the problem is a leaked credential or a security defect, follow
  `SECURITY.md` first and release second.

## After a release

- Confirm the docs site rebuilt and shows the new version.
- Confirm the Release body rendered the changelog section, not the fallback.
- Open the next `## [Unreleased]` section if the changelog step did not already.
- Update the install instructions in `README.md` only once the package is
  genuinely installable from the index being described. Documented availability
  that does not exist is the one release mistake this project cannot afford.
