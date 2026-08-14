"""Small, dependency-free terminal presentation helpers."""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any


def supports_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _style(value: str, code: str) -> str:
    if not supports_color():
        return value
    return f"\x1b[{code}m{value}\x1b[0m"


def _safe_text(value: Any) -> str:
    """Make untrusted text inert and single-line in terminals and CI logs."""

    rendered: list[str] = []
    for character in str(value):
        codepoint = ord(character)
        category = unicodedata.category(character)
        if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
            rendered.append(f"\\x{codepoint:02x}")
        elif category in {"Cf", "Cs", "Zl", "Zp"}:
            width = 4 if codepoint <= 0xFFFF else 8
            rendered.append(f"\\u{codepoint:0{width}x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def title(value: str) -> None:
    print(_style(_safe_text(value), "1;36"))


def success(value: str) -> None:
    print(f"{_style('OK', '1;32')}  {_safe_text(value)}")


def warning(value: str) -> None:
    print(f"{_style('WARN', '1;33')}  {_safe_text(value)}")


def failure(value: str) -> None:
    print(f"{_style('FAIL', '1;31')}  {_safe_text(value)}", file=sys.stderr)


def info(value: str) -> None:
    print(f"{_style('INFO', '1;34')}  {_safe_text(value)}")


def print_json(value: Any) -> None:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> None:
    """Print a compact table that also behaves well in redirected logs."""

    header_list = [_safe_text(item) for item in headers]
    row_lists = [[_safe_text(value) for value in row] for row in rows]
    widths = [len(item) for item in header_list]
    for row in row_lists:
        if len(row) != len(widths):
            raise ValueError("every table row must have the same number of columns")
        widths = [max(current, len(value)) for current, value in zip(widths, row, strict=True)]

    def render(row: list[str]) -> str:
        return "  ".join(
            value.ljust(width) for value, width in zip(row, widths, strict=True)
        ).rstrip()

    print(_style(render(header_list), "1"))
    print(render(["-" * width for width in widths]))
    for row in row_lists:
        print(render(row))


def flatten_mapping(mapping: Mapping[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, value in mapping.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            rows.extend(flatten_mapping(value, full_key))
        else:
            rows.append((full_key, value))
    return rows
