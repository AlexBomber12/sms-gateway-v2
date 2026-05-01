#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check ."
ruff check .

echo "==> ruff format --check ."
ruff format --check .

echo "==> mypy src"
mypy src

echo "==> pytest --cov-fail-under=100"
pytest --cov-fail-under=100

echo "==> ci.sh OK"
