# Releasing

Maintainer runbook for cutting a RepoTrials release: what each workflow
actually does, what still has to be done by hand, and how a third party
verifies the result.

Current state, as of 2026-08-15:

- **`v0.1.0` is released.** The tag points at commit `7bc0ce5`. The GitHub
  Release carries three assets: `repotrials-0.1.0-py3-none-any.whl`,
  `repotrials-0.1.0.tar.gz`, and `SHA256SUMS`.
- **The container is published.** `ghcr.io/pozzitiv4ik/repo-trials:0.1.0`,
  Linux/amd64, with a matching `sha-7bc0ce50...` tag.
- **RepoTrials is not on PyPI.** `pip install repotrials` does not work. The
  `publish-pypi` job exists but is dormant, and nothing publishes to an index
  until the two manual steps below are both done.

## What `release.yml` does

Trigger: a pushed tag matching `v*`. That is the only trigger — there is no
`workflow_dispatch`, no TestPyPI path, and no rehearsal target.

Job `build-and-publish`, on `ubuntu-latest`, with `contents: write` and a
15-minute timeout:

1. Checks out the tagged tree with `persist-credentials: false`.
2. Sets up Python 3.13 with the pip cache.
3. **Version guard.** Reads `[project] version` from `pyproject.toml` with
   `tomllib` and fails unless `GITHUB_REF_NAME` is exactly `v` plus that
   version. This is the workflow's only version check: `__version__`,
   `CITATION.cff`, and the `Dockerfile` are never inspected.
4. Runs `python -m build`, then `python -m twine check dist/*` — metadata
   validation, not `--strict`.
5. Writes checksums: `cd dist && sha256sum *.whl *.tar.gz > SHA256SUMS`.
6. Runs `gh release view "$TAG"`, and only if that fails, creates the release
   with `--verify-tag --title "RepoTrials $TAG" --generate-notes`. Notes come
   from GitHub's commit and pull-request generator, not from `CHANGELOG.md`.
   If a release for the tag already exists, the workflow leaves its title and
   body untouched and only attaches assets — which is why the v0.1.0 release
   carries its own title rather than the workflow's default.
7. Uploads `dist/*.whl`, `dist/*.tar.gz`, and `dist/SHA256SUMS` to the release
   with `--clobber`.
8. Uploads `dist/` as the `release-distributions` artifact — 7-day retention,
   `if-no-files-found: error` — for the optional PyPI job.

What it does **not** do: no tests, no linter, no type check, no wheel-install
smoke test, and no attestation. `CI` does not run on tags, so the tagged tree
is only as tested as the `main` commit it points at. Tag a commit whose `CI`
run is green.

## What `container.yml` does

Trigger: `workflow_dispatch` only. The job runs only when all three hold: the
repository is `PozziTiv4ik/Repo-Trials`, the dispatch ref is `main`, and the
required `confirmation` input is typed exactly as `publish-v0.1.0`. Top-level
`permissions: {}`; the job takes `contents: read` and `packages: write`. Every
action is pinned to a full commit SHA, and the release coordinates —
`IMAGE`, `RELEASE_REF`, `RELEASE_SHA`, `VERSION`, and a digest-pinned
`PYTHON_IMAGE` — are hardcoded in `env:`.

1. Checks out twice: `publisher/` at the dispatch ref, which supplies the
   `Dockerfile`, and `release/` at `RELEASE_REF`, which supplies the build
   context. The image is built from tagged source using `main`'s `Dockerfile`.
2. Verifies identity: `publisher` HEAD equals `GITHUB_SHA`, `release` HEAD
   equals `RELEASE_SHA`, and `release/pyproject.toml` version equals `VERSION`.
3. Builds an audit image locally (`load: true`, `provenance: false`,
   `sbom: false`) and smoke-tests it: `--version` prints `RepoTrials 0.1.0`,
   `Config.User` is `10001:10001`, the `revision` label matches `RELEASE_SHA`,
   and inside the container uid and gid are 10001, `$HOME` is
   `/home/repotrials`, and `/workspace` is writable.
4. Logs in to `ghcr.io` with the job's `GITHUB_TOKEN`.
5. **Refuses to overwrite.** Aborts if `:$VERSION` or `:sha-$RELEASE_SHA`
   already resolves, and also aborts if it cannot tell — anything other than a
   not-found, manifest-unknown, name-unknown, or 404 response.
6. Builds and pushes both tags for `linux/amd64` with `provenance: mode=max`
   and `sbom: true`, plus OCI `created`, `revision`, `source`, `url`, and
   `version` labels.
7. Verifies what it published: both tags' raw manifests hash to the pushed
   digest, pulls by digest, repeats the runtime smoke test against the pulled
   image, and asserts that `imagetools inspect` reports a non-null `SBOM` and
   `Provenance`. Appends `Published: <image>@<digest>` to the job summary.

Consequence of step 5: a version tag can be published exactly once. There is no
re-publish. A bad image needs a new version.

## Enabling PyPI publication

The `publish-pypi` job in `release.yml` is guarded by
`if: vars.PUBLISH_TO_PYPI == 'true'`. Until that variable exists the job is
skipped, nothing is uploaded to any index, and a tag push behaves exactly as
v0.1.0 did. Do both of the following once; neither can be automated.

1. Register a **pending publisher** at
   <https://pypi.org/manage/account/publishing/>:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `repotrials` |
   | Owner | `PozziTiv4ik` |
   | Repository name | `Repo-Trials` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   The `repotrials` name is unclaimed as of 2026-08-15. The first successful
   upload claims it permanently; PyPI does not transfer names on request.

2. Settings → Secrets and variables → Actions → Variables: add
   `PUBLISH_TO_PYPI` with the value `true`.

Optionally create the `pypi` environment under Settings → Environments and give
it a required reviewer, so publication to the real index is an explicit human
decision. The environment name must stay `pypi`; it is part of what PyPI
verifies. Do not add a `PYPI_API_TOKEN` secret — trusted publishing exchanges a
short-lived OIDC token, so a stored credential would be an unused liability.

Once both are in place, the job runs after `build-and-publish` on every `v*`
tag. It downloads the `release-distributions` artifact, deletes `SHA256SUMS`
from it, attests build provenance with `actions/attest-build-provenance`
(`id-token: write`, `attestations: write`), and uploads with
`pypa/gh-action-pypi-publish` using `attestations: true` and `print-hash: true`.

To publish a version that was already tagged and released, do both steps above
and then re-run that tag's `Release` run from the Actions tab. The re-run
rebuilds from the tag — it does not reuse the expired artifact — and is
idempotent: release creation is guarded by `gh release view`, and asset upload
uses `--clobber`.

## Version locations

Bump all of these together. Only the first is enforced by a workflow.

| File | What carries the version |
| --- | --- |
| `pyproject.toml` | `[project] version` — the value `release.yml` checks against the tag |
| `src/repotrials/__init__.py` | `__version__`, read by `repotrials --version` and recorded as `repotrials_version` in every run manifest and report |
| `tests/test_cli.py` | asserts the literal string `RepoTrials 0.1.0`, so `CI` fails on a half-finished bump |
| `CITATION.cff` | `version` and `date-released` |
| `Dockerfile` | `ARG REPOTRIALS_VERSION` default |
| `CHANGELOG.md` | the new `## [x.y.z] - YYYY-MM-DD` heading and both link definitions at the bottom |
| `README.md` | the wheel download URL and the `ghcr.io/pozzitiv4ik/repo-trials:<tag>` example |
| `docs/index.md` | the wheel download URL and two `ghcr.io/pozzitiv4ik/repo-trials:<tag>` references |
| `docs/quickstart.md` | four `RepoTrials 0.1.0` sample outputs, two wheel download URLs, and two `ghcr.io/pozzitiv4ik/repo-trials:<tag>` references |
| `docs/faq.md` | the wheel download URL and two `ghcr.io/pozzitiv4ik/repo-trials:<tag>` references |
| `docs/task-format.md` | the `repotrials_version` field in the example record |
| `docs/assets/README.md` | the `v0.1.0 checkout` note under `report-preview.png` |
| `.github/workflows/container.yml` | `RELEASE_REF`, `RELEASE_SHA`, `VERSION`, the `created` label timestamp, the `concurrency` group, the confirmation string in both the input description and the job `if`, and the `repotrials:audit-vX.Y.Z` tag plus the expected `--version` strings |

Confirm with `grep -rn '0\.1\.0' --exclude-dir=.git .` before tagging, then bump
only the files in the table above. Leave every historical record alone: the
released `## [0.1.0]` section and its link definition in `CHANGELOG.md`, this
page's verification section, the FAQ's account of the first release, and the
third-party version cited at `docs/comparison.md:220`.

A stale `src/repotrials/__init__.py` is the only mismatch that corrupts data
rather than documentation: it mislabels stored run manifests and reports.

## Cutting a release

1. Update `CHANGELOG.md`: rename `## [Unreleased]` to
   `## [x.y.z] - YYYY-MM-DD`, open a fresh empty `## [Unreleased]` above it,
   and update the two link definitions at the bottom. Record CLI, schema, and
   exit-code changes explicitly — consumers are told to check `schema_version`
   and reject unknown values.
2. Bump every file in the table above.
3. Verify locally:

   ```bash
   python -m pip install -e ".[dev]"
   make check
   python scripts/demo.py
   repotrials --version
   ```

4. Open a pull request, let `CI` pass, and merge to `main`. `release.yml` runs
   no checks of its own, so this green run is the only gate.
5. Publish, by one of two paths:

   - **Tag push.** Tag a merged commit, never a local one:

     ```bash
     git switch main
     git pull --ff-only
     git tag -a v0.2.0 -m "RepoTrials 0.2.0"
     git push origin v0.2.0
     ```

     The workflow creates the release with generated notes and attaches the
     assets.
   - **Release first.** Publish a release in the GitHub UI against the new tag,
     with your own title and body. That creates and pushes the tag, the
     workflow finds the existing release, and it only attaches assets.

   Either way the tag must be `v` plus the exact `pyproject.toml` version;
   `v0.2.0` against a `0.2.1` package fails the guard before anything is built.
6. Watch the run, then verify the assets as described below.
7. Publish the container image (next section). It is not automatic.

## Publishing the container image

1. Merge an edit to `.github/workflows/container.yml` on `main` updating every
   field listed for it in the version table, and bump `PYTHON_IMAGE` to a
   current digest-pinned base while you are there.
2. Actions → Publish container → Run workflow, from `main`, typing
   `publish-vX.Y.Z` into the confirmation field.
3. Read `Published: <image>@<digest>` out of the job summary and record the
   digest. That is the value to quote wherever an immutable pull is documented.

## Verifying a published artifact

Everything here can be run by a third party. Publish the commands, not a
reassurance.

### Release assets

```bash
gh release download v0.1.0 --repo PozziTiv4ik/Repo-Trials
sha256sum --check SHA256SUMS
```

`SHA256SUMS` is produced in the same job that built the files, so it
establishes that the download is intact, not where the build came from. The
v0.1.0 assets carry **no** signed attestation: attestation happens only in the
`publish-pypi` job, and that job has never run, so `gh attestation verify`
reports none for them.

### Build provenance — only after PyPI publishing is enabled

```bash
gh release download vX.Y.Z --repo PozziTiv4ik/Repo-Trials --pattern '*.whl'
gh attestation verify --repo PozziTiv4ik/Repo-Trials \
  repotrials-X.Y.Z-py3-none-any.whl
```

The wheel on PyPI and the wheel attached to the Release are the same file with
the same digest, so one attestation covers both.

### Container image

```bash
docker buildx imagetools inspect ghcr.io/pozzitiv4ik/repo-trials:0.1.0
docker buildx imagetools inspect ghcr.io/pozzitiv4ik/repo-trials:0.1.0 \
  --format '{{json .Provenance}}'
docker buildx imagetools inspect ghcr.io/pozzitiv4ik/repo-trials:0.1.0 \
  --format '{{json .SBOM}}'
```

The 0.1.0 digest is:

```text
sha256:292bf655e882762f2affc3c4c7d1a36ef2a949d2b272ad8d24678601e2516701
```

Pull by digest rather than by tag for an immutable image:

```bash
docker run --rm \
  ghcr.io/pozzitiv4ik/repo-trials@sha256:292bf655e882762f2affc3c4c7d1a36ef2a949d2b272ad8d24678601e2516701 \
  --version
```

That prints `RepoTrials 0.1.0`; `docker image inspect` on the same digest
reports `Config.User` as `10001:10001`.

### Source distribution contents

The sdist ships the schemas, docs, and `scripts/demo.py`. `CI` asserts it on
every run, and it is worth re-checking once from the published file:

```bash
tar -tzf repotrials-0.1.0.tar.gz | grep -E 'schemas/|docs/|scripts/demo.py'
```

## Manual repository settings

No workflow can set these. Status verified 2026-08-15.

1. **Pages source.** Settings → Pages → Build and deployment → Source:
   **GitHub Actions**. Still unset — the Pages API returns 404 for this
   repository, so `docs.yml` builds the site and then fails at the deploy step
   with "Pages is not enabled", and no site is published.
2. **Homepage.** Settings → General → Website. Currently empty. Point it at the
   Pages URL, but only after step 1 has produced a live site.
3. **Social preview.** Settings → General → Social preview. Upload a 1280x640
   raster render of `docs/assets/social-preview.svg` — GitHub's uploader takes
   PNG, JPG, or GIF, not SVG. `docs/assets/README.md` has the headless-Chrome
   command for rendering it. This setting cannot be read back through the API;
   check it by opening the page.
4. **Description** and **Topics.** Both are set: the description matches the
   README's positioning line, and fifteen topics are attached. Revisit only if
   the positioning copy changes.
5. **Branch protection on `main`.** No ruleset and no protection rule exists
   today. Adding one — require a pull request, require the `CI` and `CodeQL`
   checks, require branches to be up to date, block force pushes and
   deletions — is also what moves the OpenSSF Scorecard Branch-Protection score
   off zero. Scorecard cannot read those settings with the default
   `GITHUB_TOKEN`; supplying a read-only `administration` token as
   `SCORECARD_TOKEN` and uncommenting `repo_token` in
   `.github/workflows/scorecard.yml` is optional, and leaving it unset reports
   the check as inconclusive rather than failed.

## If a release is wrong

- **Never delete or move a tag.** Task IDs, run manifests, and comparison
  digests recorded against a revision are meant to stay resolvable.
- Release assets can be replaced with `gh release upload --clobber`, and the
  release itself can be marked as a pre-release or deleted. Say what happened
  in `CHANGELOG.md`.
- A container tag cannot be replaced; `container.yml` refuses to overwrite one.
  Ship a new version.
- Once PyPI publishing is enabled, a version cannot be re-uploaded there
  either. Yank it (Manage → Yank), which hides it from new resolutions while
  leaving pinned installs working, and fix forward with a patch version.
- If the problem is a leaked credential or a security defect, follow
  `SECURITY.md` first and release second.

## After a release

1. Confirm all three assets are attached and that `SHA256SUMS` verifies.
2. Publish the container image and record its digest.
3. Open the next `## [Unreleased]` section if the changelog step did not.
4. Change install instructions only for a channel the package is genuinely
   installable from. `README.md` currently states that RepoTrials is not on
   PyPI; that line changes after a successful `publish-pypi` run, not before.
