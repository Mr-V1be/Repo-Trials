#!/usr/bin/env python3
"""Thin shim around the bundled RepoTrials demo.

The demo itself lives in :mod:`repotrials.demo` so that it ships inside the
package and can be started with ``repotrials demo`` after a normal install.
This script keeps the source-checkout entry point (``python scripts/demo.py``)
working unchanged; it requires RepoTrials to be installed, exactly as before.
"""

from __future__ import annotations

from repotrials.demo import main

if __name__ == "__main__":
    raise SystemExit(main())
