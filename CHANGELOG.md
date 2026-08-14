# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project intends to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) after its first versioned release.

## [Unreleased]

### Added

- Initial local-first Git history mining workflow for Python repositories.
- Candidate listing, validation, and review commands.
- BASE, RED, and GOLD validation model, with RED serving as the historical no-op task state.
- Local verifier-vault separation for hidden tests and reference patches.
- Generic command-based agent execution.
- JSON and HTML reporting and comparison workflow.
- Harbor-compatible task export.
- Stable Harbor v0.20.0 task-schema 1.3 export with a bounded patch-only collect hook, deletion-aware handoff, and a separate no-network verifier.
- Explicit `--unsafe-local` acknowledgement for host-local validation and the unsandboxed command runner.
- Durable running/complete run-group manifests with exact task, contract, attempt, and result membership.
- Initial architecture, methodology, task-format, threat-model, and roadmap documentation.
- Configuration reference, explicit source-install guidance, and dependency-update configuration.
- Source-distribution manifest for schemas and project documentation.
- CI coverage gate plus smoke checks for the end-to-end demo and built wheel/source distributions.

### Changed

- Replaced unhosted schema URLs with stable URN identifiers.
- Added canonical project, issue-tracker, citation, container-source, and changelog URLs.
- Updated package license metadata to the SPDX/PEP 639 form.
- Bound public task manifests to frozen contracts and exact submission paths.
- Anchored Harbor collection to a stored baseline SHA so agent-created commits do not change the exported patch.
- Aligned verifier setup ordering across local and Harbor execution: submission and hidden-test patches are applied before runtime setup and testing.
- Added Harbor Bash/toolchain preflight plus cumulative build/verifier timeout budgets.
- Aligned generated JUnit `status` handling and setup-mutation snapshots with the local verifier.
- Allowed optional skipped/xfail cases while keeping every required F2P/P2P test strictly passing.
- Rejected tasks whose setup creates a source path that the historical submission must add.
- Made local and Harbor submission allowlists exact even for Git filenames containing glob metacharacters.
- Pinned Docker validation execution to the declared Linux/amd64 target and initialized nested paths at the actual Git work-tree root.
- Treated a missing local Harbor executable as informational because RepoTrials exports tasks for a downstream runner rather than invoking Harbor itself.

### Security

- Documented that target tests and agent commands are arbitrary code and require an operator-provided isolation boundary.
- Escaped terminal and log control characters, including OSC-52, BEL, bidi/format, surrogate, and line-control characters, before console rendering.
- Hardened every managed private-state path against symlink, reparse-point, wrong-type, and path-component redirection, including fresh-clone Git exclusion repair.
- Documented that the generated Harbor agent currently runs as root inside its container unless the downstream provider overrides the task user.
- Documented the writable bind mount and image-default-user limitations of host Docker validation.
- Documented that the Harbor verifier's UID/GID 65534 test subprocess cannot normally write to its root-owned repository workspace.

There is no stable release yet.
