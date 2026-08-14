.PHONY: check test lint type build demo

check: lint type test

test:
	python -m pytest --cov=repotrials --cov-report=term-missing

lint:
	python -m ruff check .
	python -m ruff format --check .

type:
	python -m mypy src/repotrials

build:
	python -m build

demo:
	python scripts/demo.py
