# Task and result formats

RepoTrials v0.1 has four canonical JSON schemas:

- [`task-public-v1.schema.json`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/schemas/task-public-v1.schema.json) for agent-safe task metadata;
- [`task-private-v1.schema.json`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/schemas/task-private-v1.schema.json) for provenance and verifier artifacts; and
- [`result-v1.schema.json`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/schemas/result-v1.schema.json) for one trial result; and
- [`run-group-v1.schema.json`](https://github.com/PozziTiv4ik/Repo-Trials/blob/main/schemas/run-group-v1.schema.json) for the durable cohort lifecycle.

Consumers must inspect `schema_version` and reject unknown versions. The schemas are closed at the top level: fields not declared there are invalid. A few explicitly open nested objects, such as `expected`, `submission`, and parts of `result`, are extension points.

Schema `$id` values are stable `urn:repotrials:...` identifiers, not promises of a hosted retrieval endpoint. Resolve schemas from the source tree or source distribution.

## Separation rules

1. Give an agent only the public task and an exported base workspace.
2. Keep the private task, hidden-test patch, gold patch, source Git history, and vault outside the agent workspace.
3. Treat every `sha256:` reference as verifier metadata, not as a path the agent can resolve.
4. Validate repository and archive paths before materialization; reject absolute paths, parent traversal, and unsafe link targets.
5. Revalidate and assign a new task/contract digest after changing a prompt, base archive, hidden test, gold patch, policy, or environment. Recompute any externally maintained task-set digest as well.

## Public task

The public object carries the reviewed instruction, base-workspace identity, execution policy, and quality labels. This example conforms to the v1 schema:

```json
{
  "schema_version": "repotrials.task/v1",
  "task_id": "rt_7f04b0c8",
  "contract_sha256": "58a8b85fe8062c27b911c19b1e4bdeecf176782833fbe3c201d1c6589c98ab1e",
  "prompt": {
    "title": "Cache lifetime is not incremented",
    "body": "Calling the multi-argument setup path must increment the cache lifetime.",
    "visibility": "issue_only"
  },
  "base": {
    "archive_sha256": "0f4dbf87a75b3c3548c09b26f67e8e409f5f32f1468ad3902a7ad84b349dbecc",
    "image": "python:3.12-slim",
    "platform": "linux/amd64"
  },
  "profile": "python-pytest/v1",
  "policy": {
    "network": "none",
    "protected_paths": [".repotrials/**", "tests/**"],
    "submission_paths": ["src/cache.py"],
    "timeout_seconds": 1200
  },
  "quality": {
    "tier": "verified",
    "exposure": "private_unpublished",
    "environment_fidelity": "locked"
  }
}
```

Important constraints:

- `task_id` matches `^rt_[a-z0-9]{8,32}$` and must not reveal a commit identifier.
- `contract_sha256` binds the exact frozen execution and verification contract used to create the task.
- `prompt.visibility` is `issue_only`, `failing_log`, or `tests_visible`.
- `base.archive_sha256` is a 64-character lowercase digest without a `sha256:` prefix.
- The referenced base is a `git archive` snapshot, not a full checkout: historical `export-ignore` rules may omit tracked files, and submodule contents, hydrated Git LFS objects, and repository symlinks are unsupported.
- `policy.network` is `none`, `provider-only`, or `public`; the value describes the requested policy and is not proof that the host enforced it.
- `policy.submission_paths` is the exact non-empty file allowlist enforced for the agent submission in v0.1.
- `quality.tier` distinguishes automatically accepted (`auto`) from human-reviewed (`verified`) tasks.
- `quality.exposure` records contamination risk; it is not a guarantee about model training data.

## Private task oracle

The private object connects the public task to exact historical provenance and content-addressed vault artifacts:

```json
{
  "schema_version": "repotrials.oracle/v1",
  "task_id": "rt_7f04b0c8",
  "provenance": {
    "repository": "local:inventory-service",
    "base_commit": "1111111111111111111111111111111111111111",
    "fixed_commit": "2222222222222222222222222222222222222222",
    "reconstruction_method": "single_parent",
    "issue": null,
    "pull_request": null
  },
  "artifacts": {
    "base_archive": "sha256:0f4dbf87a75b3c3548c09b26f67e8e409f5f32f1468ad3902a7ad84b349dbecc",
    "hidden_test_patch": "sha256:58a8b85fe8062c27b911c19b1e4bdeecf176782833fbe3c201d1c6589c98ab1e",
    "gold_patch": "sha256:678b8c83f0ddfc47ee94fc4a366503a88e7aabc79c480faaa1902a4aa137bc4e"
  },
  "expected": {
    "fail_to_pass": ["tests.test_cache::test_multi_argument_lifetime"],
    "pass_to_pass": ["tests.test_cache::test_single_argument_lifetime"]
  },
  "validation": {
    "repetitions": 3,
    "base_stable": true,
    "red_stable": true,
    "gold_stable": true,
    "gold_resolved": true,
    "noop_resolved": false
  }
}
```

`reconstruction_method` is `pr_base_head`, `merge_first_parent`, or `single_parent`. Vault references use the `sha256:<64 lowercase hex characters>` form. `gold_patch` exists to validate the oracle and is not a privileged patch that the grader should require from an agent.

The private schema records the validation summary. Detailed phase logs and mining/review diagnostics may be persisted separately; they are not part of the stable public task contract in v0.1.

RepoTrials' internal stored `Task` also carries a frozen `repotrials.contract/v1` record. It binds the instruction digest, test/setup commands, path policies, validation environment, execution requests, limits, and expected test IDs to the task identity. The public and private JSON examples above are safe/export views, not a complete dump of that internal record.

The internal task also freezes `source_files` from the implementation side of the historical human fix. v0.1 evaluates only changes, additions, or deletions in that exact set. This is an integrity constraint, not gold-diff scoring: any patch within the set may pass if it satisfies the hidden behavioral checks, while a correct solution that needs a new helper path is conservatively rejected.

## Agent command contract

Run a generic command with:

```bash
repotrials run --agent-command "<command>" --name <label> --unsafe-local
```

Harbor is not required for this path. `--unsafe-local` explicitly acknowledges that the trusted operator command may execute arbitrary code with the invoking user's host access and effective network; it is not a sandbox switch. RepoTrials supplies a task workspace, captures the command outcome and resulting changes, then grades those changes in a separate verifier context. Consult `repotrials run --help` for the current prompt/environment handoff and optional limits.

An agent should:

- edit only inside its supplied workspace;
- change only the implementation paths allowed by the task's frozen `source_files` set; new helper paths are not accepted in v0.1;
- leave the workspace in the desired final state;
- avoid persistent background processes; and
- not assume that its own exit status determines verifier success.

`--name` is a human label, not a stable model identity. Record model, agent, prompt, tools, and budget metadata separately for published comparisons.

## Trial result

The result schema stores one task attempt:

```json
{
  "schema_version": "repotrials.run/v1",
  "run_id": "run-4b9e9e32ab8a4cdfaf12",
  "run_group": "agent-a-20260814-120000-a1b2c3",
  "attempt": 1,
  "task_id": "rt_7f04b0c8",
  "system": {
    "name": "agent-a",
    "model": "provider/model-revision",
    "agent_command_sha256": "a5345e20b2c463dbcc7a6ef437b08d180b91f8a28a19d87fc56dbf93efa7f7f3",
    "repotrials_version": "0.1.0",
    "task_digest": "678b8c83f0ddfc47ee94fc4a366503a88e7aabc79c480faaa1902a4aa137bc4e",
    "task_contract_sha256": "58a8b85fe8062c27b911c19b1e4bdeecf176782833fbe3c201d1c6589c98ab1e",
    "execution_profile_sha256": "0f4dbf87a75b3c3548c09b26f67e8e409f5f32f1468ad3902a7ad84b349dbecc"
  },
  "submission": {
    "patch_sha256": "b07b4ca20d6dcbaa5466dc16c5362a00502abf206dbec24974749d8b0599d374"
  },
  "result": {
    "resolved": false,
    "failure_kind": "tests_failed",
    "wall_seconds": 412.4,
    "cost_usd": null,
    "f2p": {"tests.test_cache::test_multi_argument_lifetime": "failed"},
    "p2p": {"tests.test_cache::test_single_argument_lifetime": "passed"},
    "integrity_passed": true
  }
}
```

`agent_command_sha256` stores a digest rather than exposing a possibly sensitive command in portable results. `task_digest`, `task_contract_sha256`, and `execution_profile_sha256` bind the attempt to portable task content, its frozen contract, and the recorded execution profile. `f2p` and `p2p` map normalized JUnit test identifiers to their observed statuses. An infrastructure or verifier failure must be labeled through `failure_kind` and must not be silently counted as an ordinary model failure. When supplied, `run --cost-usd <amount>` records that constant on each task attempt; it is not a run-group total. JSON is the automation interface; HTML reports are presentations of stored results.

## Run-group manifest

Before executing the first attempt, RepoTrials atomically writes a `running` cohort manifest under `.repotrials/runs/<run-group>/group.json`. It freezes the ordered task set, task and contract digests, attempt budget, expected trial count, agent/model label, and creation time. Only after every expected result is durable does RepoTrials replace it with a `complete` manifest containing the exact ordered run IDs and completion time.

```json
{
  "schema_version": "repotrials.run-group/v1",
  "run_group": "agent-a-20260814-120000-a1b2c3",
  "status": "complete",
  "task_ids": ["rt_7f04b0c8"],
  "task_digests": {
    "rt_7f04b0c8": "678b8c83f0ddfc47ee94fc4a366503a88e7aabc79c480faaa1902a4aa137bc4e"
  },
  "task_contract_digests": {
    "rt_7f04b0c8": "58a8b85fe8062c27b911c19b1e4bdeecf176782833fbe3c201d1c6589c98ab1e"
  },
  "attempts": 1,
  "expected_trial_count": 1,
  "agent": "agent-a",
  "model": "provider/model-revision",
  "created_at": "2026-08-14T12:00:00Z",
  "run_ids": ["run-4b9e9e32ab8a4cdfaf12"],
  "completed_at": "2026-08-14T12:04:13Z"
}
```

Reports and comparisons reject a missing, still-running, malformed, or result-mismatched group manifest. This prevents a crash after a subset of tasks from silently becoming a smaller successful benchmark.

## Comparison records

A published comparison should state:

- sorted task IDs and a separately computed aggregate task-set digest;
- included, excluded, and failed-to-grade counts;
- system label and complete configuration metadata;
- resolved count and rate;
- regression and failure-category counts; and
- timing or cost only when collected consistently.

Scores are comparable only when the task set, task schema, agent configuration, attempt budget, and execution environment are materially equivalent. The v0.1 comparator requires one run group per side and enforces identical task IDs, portable task-content and task-contract digests, attempt shapes, and recorded execution profiles. It does not compute an aggregate task-set digest or prove equivalence of provider-side settings omitted from the profile.

## Harbor export

```bash
repotrials export-harbor --output .repotrials/exports/harbor
```

Each exported task has this core layout:

```text
<task-id>/
|-- instruction.md
|-- task.toml
|-- environment/
|   `-- Dockerfile
`-- tests/
    |-- Dockerfile
    |-- test.sh
    `-- <verifier files>
```

When a base archive is supplied, the exporter places a validated copy in the generated agent and verifier build contexts. The generated Dockerfiles use the image frozen during task validation and attempt to add Git, Bash, and pytest 9.1.1 when they are missing. Harbor v0.20 invokes the main-service collect hook through Bash. The agent build runs frozen setup before creating its one-commit synthetic Git baseline. The verifier build may run setup to acquire dependencies, then restores the clean base tree; the runtime verifier repeats setup only after applying the submission and hidden tests. A custom image must therefore supply Python, pip, Git, and Bash or, when Git/Bash are absent, a Debian-compatible `apt-get`; use a prebuilt pinned image when those assumptions do not hold.

Each generated build receives a cumulative timeout large enough for the frozen setup sequence plus package-install overhead. The outer verifier timeout likewise covers every setup command at its individual frozen timeout, one full test-command timeout, and bounded supervisor overhead; it is intentionally larger than the per-command timeout.

The v0.1 Harbor export targets Linux containers. A task validated with the local backend on Windows or macOS has not thereby been qualified for the exported Linux image; revalidate with the matching Docker image before comparing downstream results.

Harbor's task network policy governs the running environment, not necessarily the Docker builder. The generated Git/pytest installation and frozen setup layers may use builder egress. Build in a controlled environment, or pre-bake and pin all tooling and dependencies when build-time network access is unacceptable.

`task.toml` targets stable [Harbor v0.20.0](https://github.com/harbor-framework/harbor/releases/tag/v0.20.0) and uses task schema 1.3 with `environment_mode = "separate"`. The verifier has its own no-network environment.

After the agent phase, a bounded `[[verifier.collect]]` hook stages the complete workspace diff. It reads the sealed commit from `/opt/repotrials-base-sha` rather than using the agent's current `HEAD`, so agent-created commits do not move the comparison anchor and committed changes remain part of the submission. The hook writes `/tmp/agent.patch` atomically as a bounded binary full-index diff. Harbor transfers that single patch artifact—not the raw workspace or verifier files—to the separate verifier at the same path. The verifier starts from a clean base and rejects a missing, oversized, malformed, outside-path, protected-path, symlink, or special-file submission. For an accepted patch, it applies the submission and hidden test overlay, reruns frozen setup, and then evaluates JUnit outcomes. Patch transfer preserves allowed additions, modifications, and deletions; any captured change outside the exact frozen `submission_paths` fails the attempt instead of being silently discarded.

The generated agent image currently leaves `USER root` in effect and does not set an `[agent].user` override. Unless the downstream provider overrides it, the agent runs as root inside its container. The verifier supervisor is also root so it can stage private inputs and make `/tests` and `/logs` root-only, but it drops the actual candidate test subprocess to UID/GID 65534. These measures are defense in depth and do not replace the container/provider boundary.

The verifier workspace remains root-owned and is therefore effectively read-only to that unprivileged test subprocess under the generated defaults. Suites that write caches, snapshots, databases, generated files, or other state inside the repository may fail for permission reasons. Direct such output to a temporary location, create a narrowly scoped writable location through the frozen setup when appropriate, or reject/adjust the task. This is a v0.1 compatibility limitation, not evidence that the agent patch is incorrect.

The privilege drop is not an in-process secrecy boundary: submitted Python code runs in the same test subprocess that must import the hidden tests and produce JUnit. A deliberately malicious submission may introspect or monkeypatch that process. The generated checks are designed for reproducible evaluation of normally behaving agents, not tamper-proof adversarial scoring; see the [threat model](threat-model.md#verifier-manipulation).

The `tests/` directory is verifier-side material; do not publish it as an agent-visible dataset. The gold patch is not needed to score a submission and should remain in the RepoTrials vault.

The exporter rejects unsafe relative paths and unsafe archive members, but export compatibility is not an isolation guarantee. Inspect the directory before sharing it. Harbor remains an optional downstream runner; local generic-command execution does not require it.

Export is idempotent when the selected task directory already contains an identical generated tree. It never overwrites a differing tree: a mismatch fails closed and leaves the existing export intact. Inspect and remove or relocate an older differing export explicitly before retrying.

The included test suite checks export structure and exercises the generated grader's gold and no-op paths locally. It does not launch an end-to-end Harbor or Docker runtime; run a conformance task in your own pinned downstream environment before relying on a new image or Harbor release.

See Harbor's [task-structure and network-policy reference](https://www.harborframework.com/docs/tasks) for the downstream format and execution rules.

## Compatibility policy

Before RepoTrials 1.0:

- schemas may change between minor releases;
- every persisted canonical record carries an explicit schema version;
- readers should fail with a clear migration error instead of reinterpreting data; and
- schema changes must be called out in `CHANGELOG.md`.
