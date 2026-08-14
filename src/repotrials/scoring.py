"""Binary task resolution and reproducible benchmark statistics."""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

PASSING_STATUSES = frozenset({"pass", "passed", "ok", "success", "succeeded", "xpass", "xpassed"})


def _passed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in PASSING_STATUSES
    raise TypeError(f"unsupported test outcome: {value!r}")


@dataclass(frozen=True, slots=True)
class TestSetScore:
    passed: int
    total: int
    failed: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.total < 0 or self.passed < 0 or self.passed > self.total:
            raise ValueError("invalid passed/total counts")

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.passed == self.total

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total": self.total,
            "rate": self.rate,
            "failed": list(self.failed),
        }


@dataclass(frozen=True, slots=True)
class TrialScore:
    task_id: str
    resolved: bool
    fail_to_pass: TestSetScore
    pass_to_pass: TestSetScore
    integrity_passed: bool
    failure_kind: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "resolved": self.resolved,
            "integrity_passed": self.integrity_passed,
            "failure_kind": self.failure_kind,
            "fail_to_pass": self.fail_to_pass.to_dict(),
            "pass_to_pass": self.pass_to_pass.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    low: float
    high: float
    confidence: float
    method: str = "deterministic-bootstrap"
    samples: int = 10_000
    seed: int = 0
    unit: str = "observation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "high": self.high,
            "confidence": self.confidence,
            "method": self.method,
            "samples": self.samples,
            "seed": self.seed,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class TaskScore:
    """Empirical pass@k result for one task.

    RepoTrials defines a task as resolved when *any* of its recorded attempts
    is resolved.  This is the observed pass@k for the attempts actually run,
    not the combinatorial estimator used when choosing k from a larger sample.
    """

    task_id: str
    attempts: int
    resolved_attempts: int

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id cannot be empty")
        if self.attempts < 1:
            raise ValueError("a task score requires at least one attempt")
        if self.resolved_attempts < 0 or self.resolved_attempts > self.attempts:
            raise ValueError("invalid resolved attempt count")

    @property
    def resolved(self) -> bool:
        return self.resolved_attempts > 0

    @property
    def pass_at_k(self) -> float:
        return 1.0 if self.resolved else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "resolved": self.resolved,
            "attempts": self.attempts,
            "resolved_attempts": self.resolved_attempts,
            "pass_at_k": self.pass_at_k,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkScore:
    total_tasks: int
    resolved_tasks: int
    resolve_rate: float
    confidence_interval: ConfidenceInterval
    trials: tuple[TrialScore, ...]
    tasks: tuple[TaskScore, ...]
    trial_count: int
    k: int | None
    aggregation_method: str = "pass@k"
    task_resolved_rule: str = "any_attempt_resolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "resolved_tasks": self.resolved_tasks,
            "resolve_rate": self.resolve_rate,
            "trial_count": self.trial_count,
            "aggregation": {
                "method": self.aggregation_method,
                "rule": self.task_resolved_rule,
                "k": self.k,
                "unit": "task",
            },
            "confidence_interval": self.confidence_interval.to_dict(),
            "tasks": [task.to_dict() for task in self.tasks],
            "trials": [trial.to_dict() for trial in self.trials],
        }


Outcomes = Mapping[str, Any] | Iterable[Any]


def _score_outcomes(values: Outcomes, label: str) -> TestSetScore:
    if isinstance(values, Mapping):
        items = [(str(name), outcome) for name, outcome in values.items()]
    else:
        items = [(f"{label}[{index}]", outcome) for index, outcome in enumerate(values)]
    if not items:
        raise ValueError(f"{label} must contain at least one test")
    failed = tuple(name for name, outcome in items if not _passed(outcome))
    return TestSetScore(len(items) - len(failed), len(items), failed)


def score_trial(
    task_id: str,
    fail_to_pass: Outcomes,
    pass_to_pass: Outcomes,
    *,
    integrity_passed: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> TrialScore:
    """Score a task; every F2P and P2P test must pass for resolution."""

    if not task_id:
        raise ValueError("task_id cannot be empty")
    f2p = _score_outcomes(fail_to_pass, "f2p")
    p2p = _score_outcomes(pass_to_pass, "p2p")
    resolved = bool(integrity_passed and f2p.complete and p2p.complete)
    if not integrity_passed:
        failure_kind = "integrity"
    elif not f2p.complete:
        failure_kind = "fail_to_pass"
    elif not p2p.complete:
        failure_kind = "regression"
    else:
        failure_kind = None
    return TrialScore(
        task_id=task_id,
        resolved=resolved,
        fail_to_pass=f2p,
        pass_to_pass=p2p,
        integrity_passed=bool(integrity_passed),
        failure_kind=failure_kind,
        metadata=metadata or {},
    )


def score_trial_counts(
    task_id: str,
    *,
    f2p_passed: int,
    f2p_total: int,
    p2p_passed: int,
    p2p_total: int,
    integrity_passed: bool = True,
    metadata: Mapping[str, Any] | None = None,
) -> TrialScore:
    if f2p_total < 1 or p2p_total < 1:
        raise ValueError("both F2P and P2P totals must be positive")
    f2p = TestSetScore(f2p_passed, f2p_total)
    p2p = TestSetScore(p2p_passed, p2p_total)
    resolved = integrity_passed and f2p.complete and p2p.complete
    failure = (
        "integrity"
        if not integrity_passed
        else "fail_to_pass"
        if not f2p.complete
        else "regression"
        if not p2p.complete
        else None
    )
    return TrialScore(
        task_id,
        bool(resolved),
        f2p,
        p2p,
        bool(integrity_passed),
        failure,
        metadata or {},
    )


def _percentile(sorted_values: Sequence[float], quantile: float) -> float:
    if not sorted_values:
        raise ValueError("cannot calculate a percentile of an empty sequence")
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction)


def bootstrap_confidence_interval(
    values: Iterable[float | int | bool],
    *,
    confidence: float = 0.95,
    samples: int = 10_000,
    seed: int = 0,
    unit: str = "observation",
) -> ConfidenceInterval:
    """Percentile bootstrap using a private seeded PRNG.

    Sorting and a dedicated ``random.Random`` instance make results independent
    from task input order and global random state.
    """

    observations = sorted(float(value) for value in values)
    if not observations:
        raise ValueError("at least one observation is required")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    if samples < 1:
        raise ValueError("samples must be positive")
    if not unit:
        raise ValueError("unit cannot be empty")
    if not all(math.isfinite(value) for value in observations):
        raise ValueError("observations must be finite")

    if len(observations) == 1 or observations[0] == observations[-1]:
        means = [sum(observations) / len(observations)]
    else:
        random_source = random.Random(seed)
        count = len(observations)
        means = []
        for _ in range(samples):
            total = 0.0
            for _ in range(count):
                total += observations[random_source.randrange(count)]
            means.append(total / count)
        means.sort()

    alpha = (1.0 - confidence) / 2.0
    return ConfidenceInterval(
        low=_percentile(means, alpha),
        high=_percentile(means, 1.0 - alpha),
        confidence=confidence,
        samples=samples,
        seed=seed,
        unit=unit,
    )


def aggregate_scores(
    trials: Iterable[TrialScore],
    *,
    confidence: float = 0.95,
    bootstrap_samples: int = 10_000,
    seed: int = 0,
) -> BenchmarkScore:
    ordered = tuple(sorted(trials, key=_trial_sort_key))
    if not ordered:
        raise ValueError("at least one trial score is required")

    grouped: dict[str, list[TrialScore]] = {}
    for trial in ordered:
        grouped.setdefault(trial.task_id, []).append(trial)
    tasks = tuple(
        TaskScore(
            task_id=task_id,
            attempts=len(task_trials),
            resolved_attempts=sum(trial.resolved for trial in task_trials),
        )
        for task_id, task_trials in sorted(grouped.items(), key=lambda item: task_sort_key(item[0]))
    )
    attempt_counts = {task.attempts for task in tasks}
    if len(attempt_counts) != 1:
        raise ValueError(
            "pass@k requires the same number of attempts for every task; "
            f"observed {sorted(attempt_counts)}"
        )
    values = [task.pass_at_k for task in tasks]
    resolved = sum(values)
    interval = bootstrap_confidence_interval(
        values,
        confidence=confidence,
        samples=bootstrap_samples,
        seed=seed,
        unit="task",
    )
    return BenchmarkScore(
        total_tasks=len(tasks),
        resolved_tasks=int(resolved),
        resolve_rate=resolved / len(tasks),
        confidence_interval=interval,
        trials=ordered,
        tasks=tasks,
        trial_count=len(ordered),
        k=next(iter(attempt_counts)),
    )


def _trial_sort_key(trial: TrialScore) -> tuple[str, str, int, str]:
    raw_attempt = trial.metadata.get("attempt")
    attempt = (
        raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else 0
    )
    run_id = str(trial.metadata.get("run_id", ""))
    folded, original = task_sort_key(trial.task_id)
    return folded, original, attempt, run_id


def task_sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


__all__ = [
    "PASSING_STATUSES",
    "BenchmarkScore",
    "ConfidenceInterval",
    "TaskScore",
    "TestSetScore",
    "TrialScore",
    "aggregate_scores",
    "bootstrap_confidence_interval",
    "score_trial",
    "score_trial_counts",
]
