.PHONY: install dev test lint run doctor clean

install:
	./scripts/install.sh

dev:
	python3 -m venv --system-site-packages .venv
	.venv/bin/pip install -e ".[speech,dev]"

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/ruff check .

run:
	.venv/bin/tuxflow app

doctor:
	.venv/bin/tuxflow doctor

clean:
	find src tests -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf build dist .pytest_cache
