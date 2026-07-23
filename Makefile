# Atajos de desarrollo (Linux/macOS). En Windows usa setup.ps1 o pip directamente.
PYTHON ?= python3

.PHONY: install dev test run help

help:
	@echo "make install  - instala aurclips (pip install -e .)"
	@echo "make dev      - instala con dependencias de test (.[dev])"
	@echo "make test     - corre la suite (pytest)"
	@echo "make run      - corrida diaria (ingesta, recorte, subida)"

install:
	$(PYTHON) -m pip install -e .

dev:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m aurclips run
