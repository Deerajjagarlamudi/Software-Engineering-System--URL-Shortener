.PHONY: setup test quality demo

setup:
	uv sync --all-extras

test:
	uv run pytest -q --cov=app --cov-report=term-missing

quality:
	uv run ruff check app tests
	uv run mypy app

demo:
	uv run uvicorn app.main:app --reload
