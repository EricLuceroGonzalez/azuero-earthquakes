.DEFAULT_GOAL := help
.PHONY: help setup test lint format check diagrams clean fetch sounds render render-vertical preview preview-vertical video

# Variables de .env (por ejemplo ELEVENLABS_API_KEY) disponibles para los comandos.
-include .env
export

RUN := uv run python -m sismos_azuero

help:  ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## Crea el entorno e instala dependencias (uv)
	uv sync
	uv run pre-commit install || true

test:  ## Ejecuta los tests con cobertura
	uv run pytest

lint:  ## Revisa estilo e imports (sin modificar)
	uv run ruff check .
	uv run ruff format --check .

format:  ## Formatea y ordena imports
	uv run ruff check --fix .
	uv run ruff format .

check: lint test  ## lint + tests (lo que corre en CI)

diagrams:  ## Diagramas UML con pyreverse (requiere graphviz)
	mkdir -p docs/diagrams
	uv run pyreverse -o png -p sismos_azuero -d docs/diagrams src/sismos_azuero

fetch:  ## Descarga el catálogo del USGS -> data/sismos.csv
	$(RUN) fetch

sounds:  ## Genera el banco de sonidos con ElevenLabs -> sounds/
	@test -n "$$ELEVENLABS_API_KEY" || (echo "Falta ELEVENLABS_API_KEY (ponla en .env)" && exit 1)
	$(RUN) sounds

render:  ## Video completo -> out/sismos_panama.mp4
	$(RUN) render

render-vertical:  ## Video vertical 1080x1920 -> out/sismos_panama_vertical.mp4
	$(RUN) render --vertical

preview:  ## Prueba rápida con los últimos 500 sismos -> out/preview.mp4
	$(RUN) render --limit 500 --duration 8 --intro 1 --outro 1 --output preview.mp4

preview-vertical:  ## Prueba rápida en vertical -> out/preview_vertical.mp4
	$(RUN) render --vertical --limit 500 --duration 8 --intro 1 --outro 1 --output preview_vertical.mp4

video: fetch sounds render  ## fetch + sounds + render

clean:  ## Borra cachés y artefactos
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
	rm -rf .coverage htmlcov dist build
