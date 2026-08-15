# Release guide

This guide is for maintainers publishing an auditable RepoTrials release.

## One-time setup

1. Create the `repotrials` project on PyPI and configure a trusted publisher for this GitHub repository, `.github/workflows/release.yml`, and the `pypi` environment.
2. Create a protected GitHub environment named `pypi`; require reviewer approval if the repository's release policy calls for it.
3. Allow GitHub Actions to publish packages and create attestations.
4. Confirm that the default branch is green on Linux and Windows.

The release workflow uses GitHub OIDC. Do not add a long-lived PyPI token to repository secrets.

## Prepare a version

1. Update the version in `pyproject.toml` and `src/repotrials/__init__.py`.
2. Move the relevant `CHANGELOG.md` entries under a dated version heading.
3. Run the complete local gate:

   ```bash
   python -m pip install --upgrade build twine
   make check
   python -m build
   python -m twine check dist/*
   repotrials demo
   ```

4. Merge the release change and wait for CI and CodeQL.
5. Create and push a signed tag matching the package version, for example `v0.1.0`.

## Automated outputs

A `v*` tag starts `.github/workflows/release.yml`, which:

- verifies that the tag and package versions match;
- builds and checks the wheel and source distribution;
- generates a build-provenance attestation;
- publishes to PyPI with trusted publishing;
- creates a GitHub Release with the distributions and generated notes; and
- publishes tagged OCI images to GitHub Container Registry.

The GitHub Release and container jobs are independent of the PyPI job, so their logs remain useful if one registry is temporarily unavailable. Do not recreate or overwrite an already published version; fix forward with a new version.

## Post-release verification

Install into clean environments from each public channel and run the same proof:

```bash
python -m venv /tmp/repotrials-release-check
/tmp/repotrials-release-check/bin/python -m pip install repotrials==<version>
/tmp/repotrials-release-check/bin/repotrials --version
/tmp/repotrials-release-check/bin/repotrials demo

docker run --rm ghcr.io/pozzitiv4ik/repo-trials:<version> --version
```

Then verify the PyPI provenance, GitHub artifact attestation, image digest, README links, and changelog. Announcements should link to the exact release and describe verified capabilities without fabricated adoption or benchmark claims.
