# Python version management via pyenv or asdf.
# The .python-version file pins the required Python version.
# Run `pyenv install` or `asdf install` to ensure it's available.

.PHONY: install test test-snapshots snapshot-update lint format mypy bandit check clean run changelog

install:
	pip install -r requirements.lock

test:
	python -m pytest tests/ --tb=short -q

test-snapshots:
	python -m pytest tests/test_snapshots.py --tb=short -q

snapshot-update:
	python -m pytest tests/test_snapshots.py --snapshot-update --tb=short -q

lint:
	ruff check .

format:
	ruff format .

mypy:
	mypy filter/ --ignore-missing-imports --follow-imports=skip

bandit:
	bandit --skip B101,B104,B301 --recursive filter/

check: lint format mypy bandit
	@echo "All checks passed."

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +

run:
	streamlit run filter/browse/app.py

changelog:
	@echo "# Changelog\n" > CHANGELOG.md.tmp
	@git tag --sort=-creatordate | while read tag; do \
		echo "## $$tag"; \
		echo ""; \
		git log --oneline --no-merges $$tag...$$prev_tag 2>/dev/null | sed 's/^/- /'; \
		echo ""; \
		prev_tag=$$tag; \
	done >> CHANGELOG.md.tmp
	@mv CHANGELOG.md.tmp CHANGELOG.md
