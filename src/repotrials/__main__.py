"""Allow ``python -m repotrials`` to behave like the console script."""

from repotrials.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
