.PHONY: install test lint run clean

PYTHON = .venv/bin/python
PYTEST = .venv/bin/pytest
UVICORN = .venv/bin/uvicorn

install:
	uv pip install -r requirements.txt

test:
	$(PYTEST) tests/ -v --cov=src --cov-report=term-missing

test-unit:
	$(PYTEST) tests/unit/ -v

test-integration:
	$(PYTEST) tests/integration/ -v

test-edge:
	$(PYTEST) tests/edge_case/ -v

test-perf:
	$(PYTEST) tests/performance/ -v

run:
	$(UVICORN) src.api.app:app --host 0.0.0.0 --port 8000 --reload

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage htmlcov
