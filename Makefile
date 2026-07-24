.PHONY: install test lint format mypy bandit check clean run

install:
	pip install -r requirements.lock

test:
	python -m pytest tests/ --tb=short -q

lint:
	ruff check .

format:
	ruff format .

mypy:
	mypy filter_app/ --ignore-missing-imports --follow-imports=skip

bandit:
	bandit --skip B101,B104,B301 --recursive filter_app/

check: lint format mypy bandit
	@echo "All checks passed."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +

run:
	streamlit run filter_app/streamlit_app.py
