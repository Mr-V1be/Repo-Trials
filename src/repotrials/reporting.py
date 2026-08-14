"""JSON and self-contained static HTML reports for RepoTrials runs."""

from __future__ import annotations

import dataclasses
import html
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "repotrials.report/v1"


def _plain(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain(value.to_dict())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_report(
    summary: Any,
    trials: Iterable[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable serializable report envelope.

    ``summary`` and each trial may be a mapping, a dataclass, or an object that
    implements ``to_dict``.  This keeps the report layer independent from the
    application's evolving model classes.
    """

    if trials is None:
        inferred = getattr(summary, "trials", ())
        trials = inferred if inferred is not None else ()
    return {
        "schema_version": REPORT_SCHEMA,
        "summary": _plain(summary),
        "trials": [_plain(trial) for trial in trials],
        "metadata": _plain(metadata or {}),
    }


def _atomic_write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def write_json_report(
    path: str | os.PathLike[str],
    summary: Any,
    trials: Iterable[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    report = build_report(summary, trials, metadata)
    payload = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    return _atomic_write(Path(path), payload)


def _lookup(mapping: Mapping[str, Any], *paths: str, default: Any = "N/A") -> Any:
    for path in paths:
        current: Any = mapping
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                break
            current = current[part]
        else:
            return current
    return default


def _display(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (Mapping, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _embedded_json(report: Mapping[str, Any]) -> str:
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def render_html_report(
    report_or_summary: Any,
    trials: Iterable[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    title: str = "RepoTrials report",
) -> str:
    if (
        isinstance(report_or_summary, Mapping)
        and report_or_summary.get("schema_version") == REPORT_SCHEMA
        and "summary" in report_or_summary
    ):
        report = _plain(report_or_summary)
    else:
        report = build_report(report_or_summary, trials, metadata)

    summary = report.get("summary", {})
    if not isinstance(summary, Mapping):
        summary = {"value": summary}
    rows = report.get("trials", [])
    total = _lookup(summary, "total_tasks", "total", default=len(rows))
    resolved = _lookup(summary, "resolved_tasks", "resolved", default="N/A")
    rate = _lookup(summary, "resolve_rate", "rate", default="N/A")
    trial_count = _lookup(summary, "trial_count", default=len(rows))
    aggregation_method = _lookup(summary, "aggregation.method", default="binary")
    aggregation_k = _lookup(summary, "aggregation.k", default=None)
    ci_low = _lookup(summary, "confidence_interval.low", default="N/A")
    ci_high = _lookup(summary, "confidence_interval.high", default="N/A")

    row_html: list[str] = []
    for raw in rows:
        row = raw if isinstance(raw, Mapping) else {"value": raw}
        resolved_value = _lookup(row, "resolved", "passed", default=False)
        css_class = "pass" if bool(resolved_value) else "fail"
        values = (
            _lookup(row, "task_id", "id"),
            _lookup(row, "metadata.attempt", "attempt"),
            resolved_value,
            _lookup(row, "fail_to_pass.rate", "f2p.rate"),
            _lookup(row, "pass_to_pass.rate", "p2p.rate"),
            _lookup(row, "integrity_passed", "integrity"),
            _lookup(row, "failure_kind", "status"),
            _lookup(row, "metadata.duration_seconds", "duration_seconds"),
            _lookup(row, "metadata.cost_usd", "cost_usd"),
        )
        cells = "".join(f"<td>{html.escape(_display(value))}</td>" for value in values)
        row_html.append(f'<tr class="{css_class}">{cells}</tr>')

    escaped_title = html.escape(title)
    cards = (
        ("Tasks", total),
        ("Attempts", trial_count),
        ("Resolved", resolved),
        (
            f"{_display(aggregation_method)}"
            + (f" (k={_display(aggregation_k)})" if aggregation_k is not None else ""),
            rate,
        ),
        ("95% CI", f"{_display(ci_low)} - {_display(ci_high)}"),
    )
    cards_html = "".join(
        f'<section class="card"><span>{html.escape(label)}</span>'
        f"<strong>{html.escape(_display(value))}</strong></section>"
        for label, value in cards
    )
    table_body = "\n".join(row_html) or '<tr><td colspan="9">No trials</td></tr>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escaped_title}</title>
<style>
:root{{--bg:#0b1020;--panel:#151c31;--line:#2c3654;--text:#eef2ff;--muted:#9ba8c7;--good:#41d19a;--bad:#ff7188}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1200px;margin:auto;padding:36px 22px}}h1{{font-size:30px;margin:0 0 24px}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:26px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}}.card span{{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}}.card strong{{display:block;font-size:24px;margin-top:5px}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:12px}}table{{width:100%;border-collapse:collapse;background:var(--panel)}}th,td{{padding:11px 13px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}tr:last-child td{{border-bottom:0}}tr.pass td:nth-child(3){{color:var(--good);font-weight:700}}tr.fail td:nth-child(3){{color:var(--bad);font-weight:700}}
footer{{color:var(--muted);font-size:12px;margin-top:16px}}
</style>
</head>
<body><main>
<h1>{escaped_title}</h1>
<div class="cards">{cards_html}</div>
<div class="table-wrap"><table>
<thead><tr><th>Task</th><th>Attempt</th><th>Resolved</th><th>F2P</th><th>P2P</th><th>Integrity</th><th>Failure</th><th>Seconds</th><th>Cost</th></tr></thead>
<tbody>{table_body}</tbody>
</table></div>
<footer>Generated by RepoTrials - self-contained report</footer>
<script type="application/json" id="repotrials-data">{_embedded_json(report)}</script>
</main></body></html>
"""


def write_html_report(
    path: str | os.PathLike[str],
    summary: Any,
    trials: Iterable[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    title: str = "RepoTrials report",
) -> Path:
    page = render_html_report(summary, trials, metadata, title=title)
    return _atomic_write(Path(path), page.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ReportBundle:
    json_path: Path
    html_path: Path


def write_report_bundle(
    directory: str | os.PathLike[str],
    summary: Any,
    trials: Iterable[Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    *,
    basename: str = "report",
    title: str = "RepoTrials report",
) -> ReportBundle:
    if not basename or any(char in basename for char in "/\\\x00"):
        raise ValueError("basename must be a safe file stem")
    output = Path(directory)
    # Materialize a generator once so JSON and HTML contain identical trials.
    materialized_trials = None if trials is None else tuple(trials)
    json_path = write_json_report(
        output / f"{basename}.json", summary, materialized_trials, metadata
    )
    html_path = write_html_report(
        output / f"{basename}.html",
        summary,
        materialized_trials,
        metadata,
        title=title,
    )
    return ReportBundle(json_path, html_path)


__all__ = [
    "REPORT_SCHEMA",
    "ReportBundle",
    "build_report",
    "render_html_report",
    "write_html_report",
    "write_json_report",
    "write_report_bundle",
]
