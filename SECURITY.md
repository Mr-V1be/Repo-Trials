# Security policy

## Supported versions

RepoTrials is currently pre-release software. Security fixes are made on the default branch; there is not yet a supported stable release line.

## Reporting a vulnerability

Use the repository's enabled [private vulnerability reporting form](https://github.com/PozziTiv4ik/Repo-Trials/security/advisories/new). Do not file a public issue containing an exploit, credentials, private source code, hidden tests, or vault contents.

If private vulnerability reporting is unavailable, open a minimal [public issue](https://github.com/PozziTiv4ik/Repo-Trials/issues/new) requesting a private maintainer contact without including technical details. Do not transmit sensitive details until a private channel has been established.

Include, when possible:

- the affected RepoTrials revision;
- operating system and Python version;
- a minimal reproduction that does not contain third-party secrets;
- the expected and observed trust boundary;
- likely impact; and
- any temporary mitigation already tested.

Maintainers will acknowledge reports on a best-effort basis, investigate, coordinate a fix, and credit reporters who wish to be named. No response or remediation SLA is promised while the project is pre-release.

## Important operating assumptions

RepoTrials executes two classes of untrusted programs:

1. historical build and test commands from a target repository; and
2. user-configured coding-agent commands.

The v0.1 process isolation and vault separation are intended to reduce accidental leakage, not to contain a determined hostile program. An agent command inherits the invoking user's filesystem, process, credential, and network permissions unless the operator supplies a stronger sandbox.

Before running an evaluation:

- use a disposable clone in an isolated VM or container;
- remove cloud, package-registry, SSH, Git, and model-provider credentials that are not required;
- do not mount a Docker socket or broad host directory into the agent environment;
- disable network access when dependencies are already available;
- impose CPU, memory, process, disk, and wall-clock limits;
- back up material that cannot be reconstructed; and
- inspect the target repository's historical scripts before executing them.

The full security analysis and deployment checklist are in [docs/threat-model.md](docs/threat-model.md).

## Scope

Examples of in-scope reports include:

- hidden verifier or gold-patch material entering the agent workspace;
- path traversal or symlink escape from a RepoTrials-managed directory;
- command construction that permits unintended shell injection;
- verifier bypass that grants credit without running required tests;
- unsafe handling of secrets in logs or reports;
- artifact-digest confusion or task/result mix-ups; and
- sandbox documentation that materially misstates the implemented boundary.

The following are generally not RepoTrials vulnerabilities by themselves:

- arbitrary behavior intentionally performed by a configured agent command;
- malicious behavior in a target repository's own test suite;
- a hosted model provider retaining data according to its documented terms;
- a mined task with incomplete behavioral coverage; or
- public-history contamination of a model's training data.

Those remain important evaluation risks and should be reported as task-quality or documentation issues when appropriate.
