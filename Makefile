.PHONY: install data run test lint all

install:
	pip install -r requirements-dev.txt && pip install -e .

data:
	python -m steel_crm.cli generate-data

run:
	python -m steel_crm.cli run

test:
	pytest -v

lint:
	flake8 src tests

all: install data run test
