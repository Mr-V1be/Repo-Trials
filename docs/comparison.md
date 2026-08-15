# How RepoTrials compares to other coding-agent evaluations

This page exists so you can decide *not* to use RepoTrials quickly and for the right reasons.

Most of the projects below are older, larger, more widely used, and more externally validated than RepoTrials. Several are the better choice for most questions people ask about coding agents. RepoTrials answers one narrow question — *does this agent configuration work on this repository* — and that question is worth asking only after you have decided a public benchmark cannot answer it for you.

## How to read this page

- Every project listed was checked against its own repository, documentation site, or paper on **2026-08-15**. Anything that could not be verified from a primary source is not described here.
- Star counts, task counts, and version numbers are as of that date and will drift. Treat them as scale indicators, not current facts.
- RepoTrials is **pre-release v0.1** with no external validation, no published results, no leaderboard, and no user base. Where this page says RepoTrials differs, it is describing a design difference, not a demonstrated advantage.
- These tools are largely complementary. The reasonable posture for a team choosing an agent configuration is one public benchmark for general capability plus one private repository-specific set for transfer, not one instead of the other.

## Quick routing

| If you want to… | Use |
|---|---|
| Compare your agent against published numbers other people can check | SWE-bench Verified, SWE-bench Pro, Terminal-Bench |
| Know whether an agent generalizes across many codebases | SWE-bench Pro, SWE-rebench, SWE-bench-Live |
| Evaluate on a language that is not Python | Multi-SWE-bench, SWE-PolyBench, Aider's polyglot benchmark |
| Generate training data or RL environments at scale | SWE-smith, SWE-Gym, SWE-rebench |
| Run agents in sandboxes across cloud providers, at scale | Harbor |
| Measure terminal and systems work, not just patches | Terminal-Bench |
| Measure raw instruction-following and edit-format reliability, cheaply | Aider's polyglot benchmark |
| Get a managed answer for your codebase without building anything | Superconductor, Sigmabench |
| Own the whole loop locally, from your own history, uploading nothing | RepoTrials, RepoAgentBench |

## Public issue-resolution corpora

These share the pattern RepoTrials borrows: a real repository at a pre-fix commit, an issue description, hidden tests, and a pass/fail decision made by running tests rather than by comparing diffs. RepoTrials did not invent this pattern; SWE-bench did.

### SWE-bench

<https://github.com/SWE-bench/SWE-bench> · <https://www.swebench.com> · MIT · ~5.6k stars · actively maintained

**What it is.** The benchmark that defined the category: "Can Language Models Resolve Real-world Github Issues?" Real GitHub issues from open-source Python projects, each paired with the repository at the parent commit and the tests from the merged fix. Evaluation runs in Docker for reproducibility, with cloud execution available through `sb-cli`. The family includes Lite (a fast subset), Multimodal (visual software domains, ICLR 2025), and Multilingual.

**Who it is for.** Anyone who needs a number that the rest of the field understands.

**Use it instead of RepoTrials when** you want comparability. A SWE-bench score can be placed next to hundreds of other published scores. A RepoTrials score is meaningful only inside your organization and cannot be compared against anyone else's, by design.

**How RepoTrials differs.** RepoTrials generates the corpus rather than distributing one, from a single repository's own history, and keeps hidden tests and reference patches on the operator's machine. It requires no GitHub issue: the prompt is derived from the historical change rather than from a filed issue, which is a real downside — a mined prompt is usually less well-specified than a human-written issue report.

### SWE-bench Verified

<https://openai.com/index/introducing-swe-bench-verified/> · <https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified>

**What it is.** A 500-sample subset of SWE-bench screened by 93 professional software developers for well-specified problem statements and appropriately scoped tests. During that campaign 38.3% of samples were flagged for underspecified statements and 61.1% for tests that could reject valid solutions; roughly two thirds of the original set was filtered out.

**Who it is for.** Everyone who reported a SWE-bench number between 2024 and 2026.

**Use it instead of RepoTrials when** you need the most widely cited coding-agent number in existence, or when you want a historical baseline to compare a new model against.

**How RepoTrials differs.** RepoTrials has nothing resembling a 93-developer annotation campaign. Its validated tasks land in tier `auto`, and promotion to `verified` is a separate human command performed by whoever runs the tool. The relevant comparison is not "RepoTrials is more carefully reviewed than SWE-bench Verified" — it is emphatically less — but that RepoTrials makes the review state explicit in the report so an unreviewed number cannot be mistaken for a reviewed one.

**Important caveat, and it applies to RepoTrials too.** In February 2026 OpenAI published [Why SWE-bench Verified no longer measures frontier coding capabilities](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/), reporting that in an audit of 138 hard tasks — selected because o3 failed them consistently across 64 runs, and each reviewed by six or more engineers — 59.4% had flawed tests or problem statements, with 35.5% classified as overly narrow tests, alongside contamination signals on a further set of tasks. Top scores had also moved only from 74.9% to 80.9% over six months. See ["What the 2026 audits mean for RepoTrials"](#what-the-2026-audits-mean-for-repotrials) below; the honest reading is that human curation at that scale still left a third of hard tasks unfair, and RepoTrials has less curation than that, not more.

### SWE-bench Pro

<https://github.com/scaleapi/SWE-bench_Pro-os> · <https://scale.com/blog/swe-bench-pro> · [arXiv:2509.16941](https://arxiv.org/abs/2509.16941) · MIT · ~500 stars

**What it is.** Scale AI's harder, contamination-resistant successor: 1,865 tasks across 41 professional repositories, aimed at long-horizon enterprise-style work. Its public set draws exclusively from strong-copyleft (GPL) repositories on the theory that the licence is a legal deterrent against inclusion in training corpora; a commercial set of 276 instances comes from 18 private startup codebases obtained through partnerships. Models clearing 80–95% on SWE-bench Verified score far lower here.

**Who it is for.** Teams who found SWE-bench Verified saturated and want a harder public number.

**Use it instead of RepoTrials when** you want difficulty and contamination resistance *and* public comparability at the same time. RepoTrials gives contamination resistance but no comparability.

**How RepoTrials differs.** SWE-bench Pro treats contamination as a corpus-construction problem solved by licence choice and private partnerships. RepoTrials treats it as a property of *your* repository: if your code is private and unpublished, no model has trained on it, and no licensing argument is needed. RepoTrials records an `exposure` label per task rather than claiming contamination is eliminated.

**Caveat.** In July 2026 OpenAI published [Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations/), reporting that roughly 30% of the SWE-bench Pro tasks it examined were broken — an automated pipeline flagged 200 of 731 (27.4%) and a human annotation campaign flagged 249 (34.1%) — across overly strict tests, underspecified prompts, low-coverage tests, and misleading prompts, and retracted its earlier recommendation of the benchmark. This does not make SWE-bench Pro a bad benchmark; it makes task fairness a hard unsolved problem that no project on this page, including RepoTrials, has solved.

### SWE-bench-Live

<https://github.com/microsoft/SWE-bench-Live> · NeurIPS 2025 Datasets & Benchmarks · MIT · ~220 stars

**What it is.** Microsoft's continuously updated variant, built on an automated curation pipeline that runs from instance creation through environment setup with no manual bottleneck. The initial release held 1,319 tasks from 93 repositories, drawn from issues created since 2024, each with a dedicated Docker image; roughly 50 newly verified issues are added per month.

**Who it is for.** Anyone whose main worry is that a static benchmark released in 2023 has been memorized.

**Use it instead of RepoTrials when** your contamination concern is about *time* rather than *privacy* — you want tasks that postdate your model's cutoff, but you still want them public and comparable.

**How RepoTrials differs.** Same automated-pipeline philosophy, opposite corpus. SWE-bench-Live keeps a shared public set fresh; RepoTrials builds a private set from one repository. SWE-bench-Live is the better choice if you do not have a repository with sufficient history, or if the repository you care about is not the one you want to measure.

### SWE-rebench

<https://swe-rebench.com/> · <https://github.com/SWE-rebench> · <https://huggingface.co/datasets/nebius/SWE-rebench> · [arXiv:2505.20411](https://arxiv.org/abs/2505.20411)

**What it is.** Nebius's fully automated pipeline for continuously mining, filtering, and validating software-engineering tasks at scale. The base corpus holds more than 21,000 issue–pull-request pairs from over 3,400 Python repositories, each validated by automated environment setup and test execution; SWE-rebench-V2 extends to 32,079 samples across roughly 20 languages. The public leaderboard uses a curated subset of 860 Python tasks with pre-built Docker images and continuously updated monthly splits, and marks results as potentially contaminated when an issue predates the model's release date.

**Who it is for.** Researchers and labs who need volume, freshness, and an explicit contamination signal.

**Use it instead of RepoTrials when** you need scale or cross-repository generality. This is the single largest gap: SWE-rebench has tens of thousands of validated tasks, and your repository may yield a handful. A ranking over five tasks is not a ranking.

**How RepoTrials differs.** SWE-rebench's contamination handling is a *label* — it tells you a result may be contaminated. RepoTrials' is *structural* for private repositories and a label everywhere else. SWE-rebench's own documentation is candid that automated collection means "not every problem is guaranteed to be fully solvable or described well"; RepoTrials makes the same admission and adds a human `verified` tier that SWE-rebench's scale would make impractical.

### SWE-PolyBench

<https://github.com/amazon-science/SWE-PolyBench> · [arXiv:2504.08703](https://arxiv.org/abs/2504.08703) · MIT · ~88 stars

**What it is.** Amazon Science's multi-language, repository-level, execution-based benchmark: 2,110 instances from 21 repositories covering JavaScript (1,017), TypeScript (729), Python (199), and Java (165), spanning bug fixes, feature additions, and refactoring. It ships a stratified 500-instance subset and a 382-instance Verified subset, plus syntax-tree-based metrics that go beyond a single pass/fail bit.

**Who it is for.** Teams whose stack is not Python, and anyone who wants localization and structural metrics rather than only resolve rate.

**Use it instead of RepoTrials when** your repository is JavaScript, TypeScript, or Java. RepoTrials v0.1 cannot help you at all in that case.

**How RepoTrials differs.** RepoTrials v0.1 supports exactly one validation stack: a Git repository with Python tests and a runner that emits JUnit XML through the `{junit}` placeholder. Other languages are on the [roadmap](roadmap.md) behind an adapter interface and are not implemented.

### Multi-SWE-bench

<https://github.com/multi-swe-bench/multi-swe-bench> · [arXiv:2504.02605](https://arxiv.org/abs/2504.02605) · Apache-2.0 · ~356 stars

**What it is.** ByteDance Seed's multilingual issue-resolving benchmark: 1,632 instances across Java, TypeScript, JavaScript, Go, Rust, C, and C++, annotated from 2,456 candidates by 68 expert annotators.

**Who it is for.** Anyone evaluating agents outside the Python monoculture, with human-annotated quality rather than pipeline-only validation.

**Use it instead of RepoTrials when** you need a systems-language benchmark, or when you want per-language breakdowns across an ecosystem.

**How RepoTrials differs.** Language coverage is the whole difference. Multi-SWE-bench's 68-annotator campaign is also a scale of human review RepoTrials does not attempt; RepoTrials pushes review onto the operator and gives them a rubric rather than doing it for them.

## Task generators aimed at training

These build task instances at volume, primarily to train or reward models rather than to rank agent configurations.

### SWE-smith

<https://github.com/SWE-bench/SWE-smith> · NeurIPS 2025 D&B Spotlight · MIT · ~740 stars

**What it is.** "Scaling Data for SWE-agents": a toolkit that synthesizes essentially unlimited task instances from any GitHub repository, including deliberately introduced breakages. The published dataset holds 52,000 task instances with 26,000 agent trajectories and 250+ pre-built Docker environments, and it produced SWE-agent-LM-32B at 40.2% pass@1 on SWE-bench Verified.

**Who it is for.** People training models and verifiers.

**Use it instead of RepoTrials when** your goal is a training corpus, not a decision about which agent to deploy. SWE-smith will give you orders of magnitude more tasks from the same repository than RepoTrials will, because it can synthesize bugs rather than waiting for history to contain them.

**How RepoTrials differs.** RepoTrials v0.1 only mines historical human fixes and never synthesizes a breakage. Synthetic bugs are excellent training signal and weaker evidence about deployment: a bug your team actually shipped and actually fixed is a better proxy for the bugs you will ship next. That is a design bet, not a proven claim.

### SWE-Gym

<https://github.com/SWE-Gym/SWE-Gym> · ICML 2025 · Apache-2.0 · ~720 stars

**What it is.** The first published environment for training real-world software-engineering agents: 2,400 real tasks from 11 Python repositories with executable environments and test verification, plus a 234-instance Lite split. Fine-tuning on fewer than 500 sampled trajectories produced double-digit absolute gains on SWE-bench Verified.

**Who it is for.** Researchers training agents and verifier models.

**Use it instead of RepoTrials when** you want a ready-made, citable training environment with published baselines.

**How RepoTrials differs.** Different purpose entirely — RepoTrials produces an evaluation set, not a gym, and has no trajectory dataset or RL interface. Note also that the SWE-Gym repository has seen no pushes since July 2025; it is a strong published artifact rather than an actively evolving tool.

## Other task shapes

These evaluate agents on work that is not "resolve an issue in an existing repository." If the question you care about has this shape, RepoTrials is simply the wrong tool.

### Terminal-Bench

<https://www.tbench.ai/> · <https://github.com/harbor-framework/terminal-bench> · Stanford and the Laude Institute · Apache-2.0 · ICLR 2026

**What it is.** A benchmark for agents operating a real shell: compiling code, training a small model, configuring a server, sysadmin and security work. Each task runs in an isolated Docker sandbox with a natural-language instruction; the agent drives the terminal and success is decided by automated tests against the end state of the environment. Terminal-Bench 1.0 held 80 tasks, 2.0 holds 89 tasks manually verified by three human reviewers each, and later versions carry active leaderboards. Harbor is the official harness.

**Who it is for.** Anyone whose agents do infrastructure, tooling, or environment work rather than patching library code.

**Use it instead of RepoTrials when** the failures you care about happen outside a patch — dependency resolution, build configuration, environment setup, long tool-use chains.

**How RepoTrials differs.** RepoTrials grades exactly one thing: whether a bounded patch flips a hidden test suite from failing to passing without breaking protected tests. It has no notion of environment end-state, and per-task human verification by three reviewers is far beyond what its single-operator review model provides.

### Aider's polyglot benchmark

<https://aider.chat/docs/leaderboards/> · Aider is Apache-2.0

**What it is.** 225 challenging Exercism exercises across C++, Go, Java, JavaScript, Python, and Rust, measuring whether a model can "follow instructions and edit code successfully without human intervention." Scoring reports a first-attempt pass rate and a second pass rate after the model sees test feedback, alongside edit-format correctness and token cost. A separate refactoring leaderboard exists.

**Who it is for.** People choosing a model for an editing-heavy workflow, or debugging why an agent's edits fail to apply.

**Use it instead of RepoTrials when** you want a cheap, fast, multi-language signal about raw editing competence and instruction-following, decoupled from repository context. It is much cheaper to run than any repository-level benchmark.

**How RepoTrials differs.** Exercism exercises are self-contained and have no repository context, no existing test suite to avoid regressing, and no codebase idioms to learn. That is the point of the benchmark and also its limit relative to the question RepoTrials asks. Note that the Aider repository has been quieter since mid-2026; check the leaderboard's own freshness before treating a listed score as current.

### Commit0

<https://github.com/commit-0/commit0> · [arXiv:2412.01769](https://arxiv.org/abs/2412.01769) · MIT · ~190 stars

**What it is.** Library generation from scratch: 54 Python libraries where the agent receives an API specification and a suite of unit tests and must produce the implementation, with static-analysis and execution feedback available interactively. Pass rates remain low.

**Who it is for.** Researchers probing long-horizon generation against a specification rather than repair.

**Use it instead of RepoTrials when** you want to measure whether an agent can build something, not fix something.

**How RepoTrials differs.** RepoTrials is repair-only by construction: every task begins from a commit where a human fixed a bug and added a test. It cannot generate a from-scratch task and does not try to.

### SWE-Lancer

<https://github.com/openai/SWELancer-Benchmark> · [arXiv:2502.12115](https://arxiv.org/abs/2502.12115) · ICML 2025

**What it is.** OpenAI's benchmark of more than 1,400 real freelance software-engineering tasks sourced from Upwork and Expensify, carrying $1 million in real-world payouts and ranging from $50 bug fixes to $32,000 feature implementations. Independent tasks are graded by end-to-end tests triple-verified by experienced engineers; managerial tasks are graded against the choices the original hiring managers made. A public Diamond split is released.

**Who it is for.** Anyone who wants economic value, not resolve rate, as the unit of measurement.

**Use it instead of RepoTrials when** you want to express agent capability in dollars, or evaluate full-stack application work with end-to-end browser tests rather than unit-test transitions.

**How RepoTrials differs.** RepoTrials records wall-clock time and reported cost as diagnostic fields but does not attempt to price a task, and its grading is unit-test transitions rather than end-to-end tests. Note the SWE-Lancer repository has not been pushed since July 2025 and carries no licence file; treat it as a published research artifact.

## Harnesses rather than corpora

### Harbor

<https://github.com/harbor-framework/harbor> · "Framework for evaluating and improving agents" · Apache-2.0 · ~4.3k stars · actively developed

**What it is.** The execution layer, not a task set: run arbitrary agents (Claude Code, OpenHands, Codex CLI, and others) against arbitrary benchmarks, distribute experiments in parallel across cloud sandbox providers such as Daytona and Modal, and generate rollouts for reinforcement learning. It is the official harness for Terminal-Bench 2.0.

**Who it is for.** Anyone running evaluations at a scale or isolation level that a laptop cannot provide.

**Use it instead of RepoTrials when** you already have tasks and need to run them properly — in a real sandbox, in parallel, across providers. This is the case where the two are least in competition, because RepoTrials is not an execution platform and its `--unsafe-local` runner is explicitly not a security boundary.

**How RepoTrials differs, and how they combine.** RepoTrials constructs and validates tasks; Harbor runs them. `repotrials export-harbor` writes a sealed task in Harbor task-schema 1.3 as of Harbor v0.20.0, with a separate no-network verifier that rejects any captured path outside the task's frozen allowlist. Harbor is under active release — v0.21.0 shipped in August 2026 — so the exported schema version may lag current Harbor; check before assuming compatibility. If you need genuine isolation for an untrusted agent, exporting to Harbor is the recommended path and running locally is not.

## Evaluating on your own codebase

This is RepoTrials' actual category, and it has real competition.

### RepoAgentBench

<https://github.com/HumphreySun98/repoagentbench> · MIT · ~29 stars · v0.1.0 alpha

**What it is.** The closest analogue: "mine your merged PRs into local, contamination-free coding-agent benchmarks." It extracts PR metadata and base commit, preserves `.git` history for version-control-aware installs, splits the unified diff into `solution_tests.patch` and `solution_source.patch`, and auto-generates verification scripts for detected frameworks including pytest, Go, Cargo, and npm. It ships built-in adapters for `claude-code` and `aider` across several frontier models, plus a `mock-fix` oracle baseline, and `report`/`replay`/`diff` subcommands.

**Who it is for.** The same people RepoTrials is for.

**Use it instead of RepoTrials when** your project is not Python — its framework detection already covers Go, Cargo, and npm, which RepoTrials does not — or when you want batteries-included model adapters instead of writing a shell command, or when your workflow is PR-centric and you want the PR title and description as the task prompt. A human-written PR description is usually a better prompt than anything derivable from a commit.

**How RepoTrials differs.** Four design choices, each with a cost. RepoTrials mines raw Git history rather than requiring merged pull requests, so it works on repositories without a PR workflow but loses the PR description as prompt material. It runs independent BASE/RED/GOLD executions with configurable repeats before a task is accepted, which is slower. It keeps hidden tests and gold patches in a content-addressed vault outside the agent workspace and exports the base tree via `git archive` with no later history, where RepoAgentBench deliberately preserves `.git` — RepoTrials' choice reduces oracle exposure and breaks any task whose build reads version-control state. And it enforces a strict cohort gate before `compare` will produce a delta. Neither project has meaningful adoption; RepoAgentBench is at 29 stars and RepoTrials at zero, and both are alpha. Evaluate both.

### Superconductor

<https://www.superconductor.com/benchmark> · hosted commercial service, by Volition

**What it is.** You select exemplary pull requests from your own repositories; the platform infers a specification from each one while hiding the solution, then has each agent implement it independently in its own cloud development environment. LLM evaluators from multiple providers grade correctness, completeness, and code quality, using several providers explicitly to avoid single-model bias. Claude Code, Codex, Cursor, OpenCode, and others are supported. The first benchmark is free and includes a setup call.

**Who it is for.** Teams who want the answer, not the tool.

**Use it instead of RepoTrials when** you would rather book a call than build a pipeline, when you want code-quality judgements and not only pass/fail, or when your repository does not fit RepoTrials' Python-plus-JUnit contract. If your organization can upload its code, this is dramatically less work.

**How RepoTrials differs.** Two hard differences. Grading: RepoTrials will not use an LLM judge, because a judge's ranking cannot be audited or replayed the way a JUnit transition can — the cost is that RepoTrials is blind to code quality, maintainability, and everything a test does not assert, which is a real limitation and not a small one. Data: Superconductor runs your code in its cloud; RepoTrials uploads nothing. If your codebase legally cannot leave your infrastructure, that decides it. If it can, Superconductor's grading dimension is genuinely broader than RepoTrials'.

### Sigmabench

<https://sigmabench.com/> · hosted commercial service

**What it is.** A public coding-agent leaderboard measuring accuracy, consistency, and speed, plus a service that runs the same benchmark against your own codebase, arranged through a booked call. The service describes itself as SOC 2 compliant with read-only repository access, and reports that agent performance varies substantially between similar codebases rather than tracking language or project size.

**Who it is for.** Teams who want both a public reference point and a codebase-specific answer from one vendor.

**Use it instead of RepoTrials when** you value being able to place your codebase's result next to a public leaderboard produced by the same methodology. RepoTrials structurally cannot offer that: its whole premise is that your task set is yours.

**How RepoTrials differs.** Same axis as Superconductor: hosted and managed versus local and operator-owned, with compliance attestation on their side and no data movement on ours. Sigmabench's observation that agent performance varies 30–60% across similar codebases is, if accurate, the strongest available argument for repository-specific evaluation generally — including for using one of these services rather than RepoTrials.

## What the 2026 audits mean for RepoTrials

Two OpenAI publications in 2026 reshaped how the field talks about benchmark quality, and both cut against RepoTrials as much as they cut for it.

- [Why SWE-bench Verified no longer measures frontier coding capabilities](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) (February 2026): a 500-task set curated by 93 professional developers still contained flawed tests and problem statements in 59.4% of the 138 hard tasks audited, alongside evidence of training-data leakage.
- [Separating signal from noise in coding evaluations](https://openai.com/index/separating-signal-from-noise-coding-evaluations/) (July 2026): roughly 30% of examined SWE-bench Pro tasks were broken — 27.4% by automated analysis, 34.1% by human annotation — leading OpenAI to retract its earlier recommendation of that benchmark.

The tempting conclusion is "public benchmarks are unreliable, so mine your own." That conclusion is wrong, and RepoTrials does not make it.

What the audits actually show is that **task fairness is hard and execution-based validation does not establish it**. A task can be perfectly reproducible, perfectly discriminating between the broken and fixed code, and still be unfair — because the prompt omits an interface name the hidden test requires, or because the test asserts an implementation detail rather than a behavior. Every failure mode OpenAI documented is available to RepoTrials, and RepoTrials applies *less* human review than either audited benchmark, not more.

Three things follow, and they are the honest version of RepoTrials' pitch:

1. **Contamination.** For a genuinely private repository, contamination is structurally addressed, because the code was never published. For a public repository it is not addressed at all: removing later history, commit IDs, remotes, the gold patch, and the hidden tests from the workspace stops `git show` and does nothing about training data. RepoTrials records an `exposure` label so a contaminated number is labelled rather than denied.
2. **Fairness.** Not addressed by automation, and RepoTrials says so in its own methodology: validated tasks land in tier `auto`, promotion to `verified` is a separate human command, and reports keep the tiers distinct. [docs/methodology.md](methodology.md) ships the review rubric with named rejection triggers, including the collection-and-import failures the OpenAI audits describe. If you skip the review step, you have the same problem the audits found, with a smaller sample.
3. **Statistical power.** A 5-task result is not a benchmark. RepoTrials prints a deterministic bootstrap 95% confidence interval and a task count next to pass@k specifically so a thin result looks thin. Public benchmarks with hundreds or thousands of tasks have real power that yours will not.

## When not to use RepoTrials

Reach for something else if any of these hold.

- **Your repository is not Python.** v0.1 supports a Git repository with Python tests and a runner emitting JUnit XML through the `{junit}` placeholder. Coarse path heuristics may recognize other extensions during mining, but those are not supported validation stacks. Use SWE-PolyBench, Multi-SWE-bench, Aider's polyglot benchmark, or RepoAgentBench.
- **You need a number other people can check.** RepoTrials results are private and incomparable across organizations. That is the design. Use SWE-bench Verified, SWE-bench Pro, or Terminal-Bench.
- **You need a result today.** The path is mine, validate with repeated executions, review, then run. Validation executes historical test suites repeatedly and is slow. A hosted service or a pre-built public set is faster by a wide margin.
- **Your history is thin.** A candidate must change implementation and Python tests in one bounded, reconstructible commit, fail on the old code, pass on the historical fix, and survive repeated execution. Many repositories yield a handful of tasks, some yield none. Run `repotrials mine --limit 100` and find out before planning around it.
- **You are choosing a model in general, not a configuration for one codebase.** Cross-repository generality is exactly what public corpora measure and RepoTrials cannot.
- **You care about code quality, maintainability, or design.** RepoTrials grades JUnit transitions only. A patch that passes the hidden tests scores identically whether it is clean or appalling. Use an LLM-judged service, or a human review process, for that dimension.
- **A correct solution would need a new file.** v0.1 freezes the editable set to the implementation paths the historical fix touched, so a behaviorally valid patch that adds a helper module cannot pass. Broader reviewed allowlists are the named next [roadmap](roadmap.md) item.
- **You are evaluating an untrusted agent and need real isolation.** RepoTrials v0.1 is not a hardened sandbox; see [docs/threat-model.md](threat-model.md). Local validation and local agent runs require `--unsafe-local` every time. Export to Harbor, or run inside a sandbox provider that actually is a boundary.
- **You need terminal, infrastructure, from-scratch, or multimodal task shapes.** Use Terminal-Bench, Commit0, or SWE-bench Multimodal.
- **You cannot spend human review time.** Automated validation answers reproducible and discriminating. It does not answer fair. Skipping review means shipping the exact defect class the 2026 audits found.
- **You need stability guarantees.** v0.1 is pre-release; command names and the task schema may change. What is offered instead is content addressing: versioned JSON Schemas with a `schema_version` consumers must check, task-content and task-contract digests baked into task IDs, and a comparator that refuses mismatched cohorts. Old numbers will fail loudly rather than drift quietly, which is not the same as not breaking.

## Corrections

If anything on this page is wrong, out of date, or unfair to another project, please [open an issue](https://github.com/PozziTiv4ik/Repo-Trials/issues/new/choose) or send a pull request. Corrections about competing projects are especially welcome and will be applied without argument. This page is a credibility asset only for as long as it is accurate.
