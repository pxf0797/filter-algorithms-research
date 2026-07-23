.PHONY: install test lint clean run

install:
	pip install -r requirements.lock

test:
	python -m pytest tests/ --tb=short -q

lint:
	ruff check .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +

run:
	streamlit run filter_app/streamlit_app.py
