#!/usr/bin/env python3
"""Backward-compatible entry point for the packaged RepoTrials demo."""

from __future__ import annotations

import argparse
from pathlib import Path

from repotrials.demo import render_summary, run_demo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = run_demo(args.output)
    print(render_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
