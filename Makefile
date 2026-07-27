.PHONY: install uninstall dev test lint format run doctor clean

VENV := .venv
PY := $(VENV)/bin/python

install:
	./scripts/install.sh

uninstall:
	./scripts/uninstall.sh

# Creates or repairs $(VENV); safe to run at any time.
dev:
	./scripts/dev-env.sh

$(PY):
	./scripts/dev-env.sh

test: $(PY)
	$(PY) -m pytest

lint: $(PY)
	$(PY) -m ruff check .

format: $(PY)
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

run: $(PY)
	$(VENV)/bin/tuxflow app

doctor: $(PY)
	$(VENV)/bin/tuxflow doctor

clean:
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf build dist .pytest_cache .ruff_cache
