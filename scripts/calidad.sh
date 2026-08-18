#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ruff check .
ruff format --check .
SATYS_CONFIG_FILE=config/configuracion_local.example.json python -m unittest discover tests/
bash tests/test_ejecucion_diaria_unica.sh
