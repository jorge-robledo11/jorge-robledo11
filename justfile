set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

# Generar contexto XML del repo
context:
    repo_name="$(basename "$PWD")"; \
    npx --yes repomix@latest \
        --style xml \
        --output "${repo_name}.xml"

# Descargar logos faltantes y construir badges
assets:
    uv run python scripts/download_logos.py
    uv run python scripts/build_logos.py

# Reconstruir todos los badges
badges:
    uv run python scripts/build_logos.py --force

# Validar que todos los logos y badges estén listos para el README
verify:
    uv run python scripts/validate_assets.py

# Ejecutar tests
test:
    uv run pytest -q

# Ejecutar lint / hooks sobre todo el repo
lint:
    uv run pre-commit run --all-files

# Validación completa
check: lint test

# Limpiar caches
clean:
    rm -rf .pytest_cache .ruff_cache .mypy_cache
    find . -type d -name "__pycache__" -prune -exec rm -rf {} +
    find . -type f -name "*.pyc" -delete
