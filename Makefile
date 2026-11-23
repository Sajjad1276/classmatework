.PHONY: install dev migrate test lint format

install:
	python -m pip install -r requirements-dev.txt

dev:
	PYTHONPATH=backend python -m app.bot.main

migrate:
	PYTHONPATH=backend alembic -c backend/alembic.ini upgrade head

test:
	pytest

lint:
	ruff check backend tests

format:
	ruff check --fix backend tests
