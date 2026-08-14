# Threat model

RepoTrials handles source code, historical patches, hidden tests, arbitrary test commands, arbitrary agent commands, and potentially sensitive reports. This makes security part of the evaluation methodology, not an optional deployment detail.

## v0.1 security statement

RepoTrials v0.1 is **not a hardened sandbox**. Its local vault and separate verifier workspace reduce accidental disclosure to a normally behaving agent. They do not protect verifier material from a malicious process running with the same operating-system permissions.

Use a VM, locked-down container, or equivalent external sandbox when the repository or agent is not fully trusted.

## Assets

- private repository source and history;
- hidden test patches and fixtures;
- gold/reference patches;
- task prompts and accepted test-set membership;
- model/API credentials and package-registry credentials;
- agent transcripts, patches, logs, costs, and scores;
- host filesystem, network identity, and compute resources; and
- integrity of evaluation results.

## Actors

- a normal local operator;
- a buggy or compromised coding agent;
- a malicious target repository or historical test script;
- a model or tool provider receiving evaluation data;
- a contributor attempting to manipulate a benchmark result; and
- an accidental publisher of vault or report artifacts.

## Trust boundaries

```text
host / operator account
  |-- source repository and Git object database
  |-- RepoTrials state and vault
  |-- agent sandbox
  |     `-- exported base workspace only
  `-- verifier sandbox
        |-- candidate patch
        `-- hidden tests and evaluator command
```

The agent and verifier should not share a writable workspace. The agent sandbox should not mount the source `.git` directory or vault.

## Threats and mitigations

### Later-history disclosure

**Threat:** The agent runs `git show`, searches refs, or reads a worktree administrative file to recover the historical fix.

**Mitigation:** Build the agent workspace from an archive/export of the base tree. Do not copy the source repository's `.git`, worktree metadata, commit IDs, remotes, or task provenance into that workspace. A new synthetic repository containing only one sealed baseline commit is acceptable for normal agent Git tooling.

**Residual risk:** The model may remember public code from training, and source/package names may identify the project.

### Vault disclosure

**Threat:** The agent reads hidden tests or gold patches from the local state directory.

**Mitigation:** Keep the vault outside the agent mount and invoke grading in a fresh verifier workspace. Restrict host filesystem permissions where possible.

**Residual risk:** Workspace separation is ineffective against a same-user hostile process without OS-level isolation.

### Verifier manipulation

**Threat:** A candidate edits tests, test discovery, verifier configuration, or package hooks so required tests disappear or always pass.

**Mitigation:** Apply hidden material after the candidate patch in a fresh workspace; use evaluator-owned commands; protect verifier paths; verify expected test identities/counts; treat missing/skipped required tests as failure. The generated Harbor verifier additionally keeps `/tests` and `/logs` root-only while its test subprocess runs as UID/GID 65534.

**Residual risk:** Python candidate code necessarily executes inside the test subprocess that imports
the hidden tests. It can inspect process state, recognize hidden inputs, monkeypatch the runner, or try
to forge the JUnit file; UID separation does not isolate code within one process. Expected-ID and
suite checks catch ordinary corruption, not a determined adversarial submission. Treat v0.1 scores as
tamper-evident workflow evidence, not as an adversarial competition boundary; stronger use cases need
an independently attested, out-of-process verifier protocol that v0.1 does not provide. Test adequacy
and held-out diversity also remain review concerns.

### Command injection

**Threat:** Repository metadata, task fields, paths, or an agent label are interpolated into a shell command and interpreted as syntax.

**Mitigation:** Prefer argument arrays and direct process execution. Treat `--agent-command` as explicitly trusted operator input, never as text derived from a repository. Quote display-only commands and avoid reconstructing executable commands from logs.

**Residual risk:** The generic command intentionally allows arbitrary execution with the operator's permissions.

### Root inside the generated agent container

**Threat:** The generated Harbor image currently leaves `USER root` in effect, so an agent may have root privileges inside its task container unless the downstream provider overrides the task user. A broad mount, Docker socket, privileged container, or provider escape can turn that into host impact.

**Mitigation:** Treat the container/provider as the security boundary; run without privilege escalation, mount only the task workspace, never mount the Docker socket or host credentials, and apply provider resource/network policy. Do not equate container root with a hardened sandbox.

### Host Docker validation boundary

**Threat:** The optional Docker validation backend bind-mounts a temporary repository workspace read/write and uses the image's default user, which is commonly root. Tests can alter that mount, leave host files owned by their container UID, exploit a vulnerable runtime/kernel, or affect anything else the operator explicitly mounts.

**Mitigation:** Use a trusted image and patched host Docker runtime, keep mounts narrow, never expose the Docker socket or credentials, and run Docker itself inside a disposable VM when evaluating hostile code. Capability dropping, `no-new-privileges`, PID/CPU/memory limits, and disabled container networking reduce exposure but are not a hardened security boundary.

### Path traversal and symlink escape

**Threat:** A patch path, archive entry, symlink, or output name escapes the task root and overwrites host or vault files.

**Mitigation:** Resolve and validate paths against an explicit root; reject absolute paths, parent traversal, device paths, and unsafe symlink targets; create verifier workspaces in dedicated temporary directories; never extract unvalidated archives over the vault.

### Secrets exposure

**Threat:** Agents or tests read environment variables, Git credentials, SSH agents, cloud metadata, browser state, model keys, or files mounted from the host; logs then persist the values.

**Mitigation:** Start from an allowlisted environment, use short-lived task-specific credentials only when unavoidable, remove Git credential helpers, do not mount home directories, disable cloud metadata access, and redact known secret patterns before report generation.

**Residual risk:** Redaction cannot reliably identify every secret. The safest secret is one absent from the sandbox.

### Network exfiltration and live dependencies

**Threat:** An agent or historical test uploads source/verifier data, fetches the public gold patch, depends on an unstable endpoint, or mutates an external service.

**Mitigation:** Separate dependency acquisition from evaluation and disable outbound network access during agent/verifier execution. Use local package caches or prebuilt images where practical.

**Build-time caveat:** A runtime no-network policy does not necessarily constrain Docker image builds. Generated Harbor images may invoke package installation and frozen setup commands during build. Use a controlled builder or pre-baked pinned images; do not expose secrets to the build context or builder environment.

**Residual risk:** Hosted agent/model APIs necessarily receive the data their client sends. Review provider retention and training terms.

### Resource exhaustion

**Threat:** Fork bombs, infinite tests, disk expansion, memory pressure, large logs, or nested containers affect the host or other evaluations.

**Mitigation:** Impose wall-clock, CPU, memory, process, file-size, and disk quotas outside RepoTrials; cap captured output; terminate process groups; avoid mounting a Docker socket.

### Result tampering and artifact confusion

**Threat:** A result from one task, environment, or patch is attributed to another; cached reports are reused after task changes.

**Mitigation:** Content-address task sets and verifier artifacts; include digests in run records; write reports from immutable result records; reject mismatched task IDs/schema versions; make cache keys include candidate-patch digest and execution contract. Harbor export transfers a bounded binary patch of the complete Git diff from a stored sealed-baseline SHA rather than the raw agent workspace; the separate verifier applies it to a fresh base and rejects outside/protected paths before hidden evaluation.

### Malicious HTML/log content

**Threat:** Test output or prompts inject script/markup into an HTML report or terminal control sequences into logs.

**Mitigation:** Escape untrusted values in HTML, avoid unsafe inline script construction, strip or visibly encode control characters, and serve reports with restrictive local permissions/content policy.

### Task poisoning

**Threat:** A contributor adds a historical-looking commit/test that rewards a particular agent, leaks the solution, or creates an unfair hidden requirement.

**Mitigation:** Record provenance, require independent review for trusted sets, cluster duplicates/backports, keep reviewer decisions auditable, and report automatic vs reviewed quality separately.

### Model-training contamination

**Threat:** A model has already seen the public issue, test, or gold patch during training.

**Mitigation:** Prefer private repositories and unpublished test sets; rotate public sets; record dates and exposure risk; keep exact task IDs and gold artifacts private.

**Residual risk:** There is no reliable general test proving that a model has never seen public source code.

## Operator deployment checklist

Before `validate` or `run`:

- [ ] Work in a disposable clone, not the only copy of a repository.
- [ ] Confirm the selected history contains no secrets that must not reach the chosen model provider.
- [ ] Place the agent in a separate VM/container/user boundary if it is not fully trusted.
- [ ] Mount only the exported base workspace; do not mount the source repository's `.git`, the vault, home, or Docker socket. A generated one-commit synthetic `.git` inside the workspace is expected.
- [ ] Remove unnecessary environment variables and credential agents.
- [ ] Disable outbound network access or document why it is required.
- [ ] Apply CPU, memory, process, disk, output, and time limits.
- [ ] Inspect historical install/test scripts before execution.
- [ ] Keep vault and raw reports out of version control and public artifact uploads.
- [ ] Verify task-set and environment digests before comparing runs.

Before publishing a report:

- [ ] Remove private source excerpts, prompts, paths, usernames, and secrets.
- [ ] Do not publish hidden tests or gold patches for an active test split.
- [ ] State the model, agent, budget, task count, task-set digest, and RepoTrials revision.
- [ ] Separate infrastructure failures from model failures.
- [ ] Describe contamination risk and human-review status.

## Security non-goals

RepoTrials does not attempt to:

- safely execute hostile code without an external sandbox;
- validate a model provider's privacy claims;
- scan a repository comprehensively for secrets or malware;
- prove semantic completeness of hidden tests;
- provide multi-tenant isolation; or
- certify an agent for production use.
