# Configuration reference

`repotrials init` writes `repotrials.toml` and creates the private `.repotrials/` state directory in the target Git repository. Commands started below that directory search upward for the nearest `repotrials.toml`; `--root` selects a different starting directory.

Review the generated file before mining. Unknown keys and invalid enum values are rejected, but `doctor` is a lightweight prerequisite check rather than a proof that historical dependencies or containers are runnable. The Harbor executable is informational: RepoTrials writes a compatible export but does not invoke Harbor itself, so the downstream runner may live on another machine.

## Minimal setup

The generated defaults target a conventional Python repository using pytest:

```toml
[repository]
default_branch = "main"
exposure = "private_unpublished"

[test]
setup = []
command = "python -m pytest -q --junitxml={junit}"
source_globs = ["src/**/*.py", "*.py"]
test_globs = ["tests/**/*.py", "test/**/*.py", "**/test_*.py", "**/*_test.py"]
ignored_globs = ["docs/**", "**/*.md", "**/*.rst", "**/__pycache__/**", "**/*.lock"]

[mining]
max_files = 8
max_changed_lines = 400
require_test_changes = true
include_merges = true

[validation]
backend = "local"
repeats = 3
timeout_seconds = 600
require_pass_to_pass = false

[execution]
backend = "local"
network = "provider-only"
timeout_seconds = 1200
attempts = 3
cpus = 2.0
memory_mb = 4096
```

Pytest is not a RepoTrials runtime dependency. Install the target repository's own test dependencies in the local environment, provide them through `test.setup`, or select a prepared validation image before running `validate`.

Keep the generated `protected_paths` list unless you understand the verifier consequences. The complete defaults remain in the generated file and are authoritative for the installed version.

## Frozen task contracts

Validation copies the effective execution and verification settings into an immutable task contract. Its digest participates in the task identity, and later `run` and `export-harbor` operations use the frozen contract rather than silently inheriting edits to `repotrials.toml`. Change the configuration and revalidate the candidate when you intend to create a new task contract. Pre-contract task records are rejected with a revalidation instruction.

Validation also freezes the candidate's implementation paths as `source_files`. Local execution rejects a submission patch containing another path. Harbor transfers the complete bounded Git diff from the agent's sealed baseline, and its separate verifier rejects the submission if that diff contains an outside or protected path. This conservative v0.1 rule can reject a valid alternative that introduces a helper file. Reject such tasks during review when the frozen set is too narrow.

## Repository settings

| Key | Default | Effect in v0.1 |
|---|---:|---|
| `default_branch` | `"main"` | Descriptive default reserved for future branch selection. `repotrials mine` currently uses `HEAD` unless `--ref` is supplied. |
| `exposure` | `"private_unpublished"` | Contamination-risk label copied into task manifests. Accepted values are `private_unpublished`, `public_post_model_release`, `public_pre_model_release`, and `unknown`. |

Use `repotrials mine --ref <revision>` when `HEAD` is not the history you intend to evaluate. A shallow clone exposes only its available history; fetch the required commits before mining.

For an initial pass over a large repository, bound history traversal with `mine --limit <count>` or `mine --since <git-date>`. The latter is passed to Git, for example `12.months.ago`.

## Test settings

| Key | Default | Effect |
|---|---:|---|
| `setup` | `[]` | Frozen commands run in order. Validation runs them after each phase's patches. The local and Harbor agent baselines run setup before the agent starts; each verifier applies the submission and hidden tests before rerunning setup and then testing. |
| `command` | pytest with `--junitxml={junit}` | Test argv. `{junit}` is replaced with an evaluator-owned XML path. |
| `source_globs` | Python source patterns | Paths treated as the implementation/gold side of a historical patch. |
| `test_globs` | conventional Python test patterns | Paths treated as the hidden-test side. Test matches take precedence. |
| `ignored_globs` | docs, caches, lockfiles | Paths excluded from both patch sides. |
| `protected_paths` | tests, config, state, and CI paths | Agent changes that match these patterns fail the integrity check. |

The test command must write non-empty, parseable JUnit XML at `{junit}`. Exit status alone is insufficient for accepted tasks. Commands use one POSIX-like quoting grammar on every host and are executed as argument vectors with no implicit shell. Invoke a shell explicitly only when the target command genuinely requires shell syntax. Setup may create derived files, but validation rejects a setup that materializes a frozen submission source path absent from the clean base: such a path would be a modification in the agent's setup-sealed baseline but an addition in the patch-only verifier.

Setup commands are part of the evaluation environment. Prefer pinned dependencies or an already prepared environment, and ensure the same setup succeeds for historical base revisions. Validation rejects setup that changes or deletes an existing workspace file, or that adds a protected path; install outside the work tree when practical. Local agent setup is committed into the synthetic baseline so permitted generated files do not become part of the submitted patch.

## Mining settings

| Key | Default | Effect |
|---|---:|---|
| `max_files` | `8` | Reject commits changing more paths than this. |
| `max_changed_lines` | `400` | Reject commits whose additions plus deletions exceed this value. |
| `require_test_changes` | `true` | Require at least one added or modified test-classified path. |
| `include_merges` | `true` | Permit merge commits, reconstructed against their first parent. Review these manually. |
| `keyword_pattern` | correction-related regex | Records `keyword_match` candidate metadata. It does not filter or rank candidates in v0.1. |

Path classification is intentionally coarse. Adjust globs for nonstandard layouts, then inspect `repotrials candidates` before validation.

`repotrials mine --github` is an explicit networked enrichment step. It derives the GitHub repository slug from `remote.origin.url` and sends candidate commit SHAs to the GitHub REST API. It uses `GITHUB_TOKEN` or `GH_TOKEN` when present and can otherwise be rate-limited. Plain `repotrials mine` stays local.

## Validation settings

| Key | Default | Effect |
|---|---:|---|
| `backend` | `"local"` | `local` runs tests on the host; `docker` requests the optional Docker CLI backend. |
| `repeats` | `3` | Fresh executions of each BASE, RED, and GOLD phase. Any inconsistent normalized outcome set rejects the candidate. |
| `timeout_seconds` | `600` | Per setup, patch, or test command timeout. |
| `require_pass_to_pass` | `false` | When true, require at least one pre-existing test that passes in both RED and GOLD. |
| `docker_image` | `"python:3.12-slim"` | Image reference used by Docker validation and as the default for Harbor export. |

Local validation executes arbitrary historical setup and tests with the current user's permissions and inherited environment. Use a disposable clone and an external isolation boundary for untrusted repositories.

Docker validation is opt-in and requires a working host Docker CLI and daemon. Each repeated BASE, RED, or GOLD phase gets one short-lived container; patch application, setup, and testing share that container, so installed dependencies persist for the rest of that phase. The container is removed after the phase and force-removed after a command timeout. The repository workspace is bind-mounted read/write, and network mode is `none`.

The validation backend does not set a container user, so the image default applies and is commonly root. It drops Linux capabilities, enables `no-new-privileges`, limits processes, CPU, and memory, and disables container networking, but those controls do not make hostile tests safe: the container can still modify the mounted workspace, may leave files owned by its UID on the host, and shares the host kernel and Docker trust boundary. Do not expose the Docker socket, credentials, or unrelated host paths.

Use a prebuilt, pinned `docker_image` that contains Git, Python, and the target test tooling. Phase patches are applied with `git apply` before setup begins, so setup cannot provide Git. Setup may install other dependencies only when they are already available without network access. The default `python:3.12-slim` is a starting image reference, not a promise that an arbitrary repository's Docker validation works without customization.

Local validation requires an explicit `--unsafe-local` acknowledgement because historical setup and tests execute with the invoking user's host permissions. The included CI tests cover Docker command/session construction and timeout cleanup without launching a Docker daemon. Qualify the chosen image with a real `validate --backend docker` run in your environment before treating its results as portable.

## Execution settings

| Key | Default | Effect in v0.1 |
|---|---:|---|
| `backend` | `"local"` | `local` selects the generic host command runner. `harbor` freezes tasks for export and prevents accidental local execution; run them downstream after `export-harbor`. |
| `network` | `"provider-only"` | Requested policy recorded in manifests. It is not enforced locally. Harbor maps `none` to no network, `public` to public egress, and `provider-only` to an empty allowlist (deny all in v0.1). |
| `timeout_seconds` | `1200` | Timeout for one local agent attempt. |
| `attempts` | `3` | Attempts per accepted task. Validation freezes this budget; `run --attempts` is accepted only when it equals the frozen value. Reports compute empirical task-level `pass@k`. |
| `cpus` | `2.0` | Resource request used by container-backed validation/export, not enforced locally. |
| `memory_mb` | `4096` | Resource request used by container-backed validation/export, not enforced locally. |

Generic command execution is explicit:

```bash
repotrials run --agent-command "my-agent --workspace {workspace} --prompt {instruction}" --name my-agent --unsafe-local
```

`--unsafe-local` is a required acknowledgement, not an isolation feature. The command receives `REPOTRIALS_WORKSPACE`, `REPOTRIALS_INSTRUCTION`, `REPOTRIALS_INSTRUCTION_PATH`, and `REPOTRIALS_TASK_ID`. `{workspace}` and `{instruction}` expand to the workspace and instruction-file paths. The command runs inside the workspace, which contains a new one-commit synthetic Git repository but no source remote or later history. `run --cost-usd <amount>` records that same amount on each individual task attempt; it is not a run-group total.

Harbor export uses the validation image and all other settings frozen into each task contract; change `repotrials.toml` and revalidate to use a different image. Generated agent and verifier Dockerfiles attempt to install Git, Bash, and pytest 9.1.1 if missing. The agent build runs frozen setup before sealing its baseline. The verifier build may run setup to acquire dependencies, then restores a clean base tree; at grading time the separate no-network verifier applies the submission and hidden tests before rerunning the same frozen setup and testing. Those generated Dockerfiles assume Python/pip and, when Git or Bash is absent, a Debian-compatible `apt-get`. Exported build and whole-verifier budgets are cumulative across the frozen per-command setup and test limits rather than reusing a single phase timeout. Actual Harbor execution remains the downstream runner's responsibility.

The generated v0.1 image leaves `USER root` in effect and does not set an `[agent].user` override. Unless the selected Harbor provider imposes a different policy, the agent therefore runs as root **inside its container**. This is not host-root authority by itself, but it makes the container/provider boundary, narrow mounts, and the absence of a Docker socket or host credentials essential.

The separate verifier's supervisor stays root, protects `/tests` and `/logs`, and drops the candidate test subprocess to UID/GID 65534. Its repository workspace remains root-owned and is effectively read-only to that subprocess. Tests that require in-tree caches or generated state may therefore fail under Harbor even when they passed during validation. Configure those outputs to use a temporary path, have frozen setup create only the narrow writable location the suite needs, or reject/adjust the task before comparison. v0.1 does not automatically normalize this behavior.

The stable Harbor v0.20.0 schema-1.3 export uses a bounded `[[verifier.collect]]` hook after the agent phase. It stages the complete workspace diff, reads the sealed baseline SHA from `/opt/repotrials-base-sha`, and atomically emits `/tmp/agent.patch` as one binary full-index diff. The stored SHA remains the anchor even when the agent makes one or more commits, and the patch represents additions, modifications, and deletions. Harbor transfers only that patch into the separate verifier. The verifier rejects a missing, malformed, oversized, outside-path, or protected-path submission, applies an accepted submission and the hidden test patch to a clean base, reruns frozen setup, then enforces the frozen file/size and JUnit policy before scoring.

The generated Harbor task targets Linux. Local validation on another operating system is not evidence that this image is equivalent; use Docker validation with that image before treating Harbor results as comparable.

Runtime `network_mode` does not by itself disable Docker build networking. The generated dependency and setup layers may use builder egress; use a controlled builder or a pre-baked pinned image when those steps must be offline.

## Private state and version control

`.repotrials/` may contain source archives, hidden tests, gold patches, raw output, and reports. `init` adds it to the repository-local Git exclude file without editing the project's `.gitignore`. Do not publish or broadly mount that directory.

`repotrials.toml` contains the benchmark policy rather than oracle material and can normally be reviewed and committed. Keep secrets out of it; setup and agent commands inherit the process environment when run locally.

See [the methodology](methodology.md) for acceptance rules and [the threat model](threat-model.md) for isolation guidance.
