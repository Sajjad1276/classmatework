.PHONY: install dev test lint format api frontend

install:
	python -m pip install -r requirements-dev.txt

dev:
	uvicorn app.main:app --app-dir backend --reload --host 0.0.0.0 --port 8000

api:
	uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000

test:
	pytest

lint:
	ruff check backend tests

format:
	ruff check --fix backend tests

frontend:
	cd frontend && npm install && npm run dev
