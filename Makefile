.PHONY: all test coverage diagram lint format typecheck clean \
		build check publish-test publish version bump tag qa

all: test

test:
	@echo "running tests"
	@uv run pytest

coverage:
	@echo "generating test coverage report"
	@uv run pytest --cov-report=html:cov_html --cov-report=term-missing --cov=buylog tests

diagram:
	@echo "generating entity-relation diagram to 'docs' folder"
	@uv run python scripts/gen_diagram.py

lint:
	@uv run ruff check --fix src/ tests/ scripts/

typecheck:
	@uv run mypy src/

format:
	@uv run ruff format src/ tests/ scripts/

qa: test lint typecheck format version

clean:
	@find . | grep -E "(__pycache__|\.pyc|\.pyo$/)" | xargs rm -rf
	@rm -rf .pytest_cache
	@rm -rf .coverage cov_html
	@rm -rf dist build *.egg-info src/*.egg-info

build: clean
	@echo "building wheel and sdist"
	@uv build
	@uv run twine check dist/*

check:
	@echo "checking distribution with twine"
	@uv run twine check dist/*

publish-test: check
	@echo "uploading to TestPyPI"
	@uv run twine upload --repository testpypi dist/*

publish: check
	@echo "uploading to PyPI"
	@uv run twine upload dist/*

version:
	@uv run python scripts/version.py

bump:
	@uv run python scripts/version.py bump

tag:
	@uv run python scripts/version.py tag
