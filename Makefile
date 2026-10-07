PYTHON ?= python3

.PHONY: run test lint format check build

run:
	$(PYTHON) -m noteworthy_app.server

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

check: lint test

build:
	$(PYTHON) -m build
