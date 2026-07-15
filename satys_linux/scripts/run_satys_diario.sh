#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="${SATYS_PROJECT_DIR:-/data/satys/Automatizacion-SATyS}"
PYTHON_BIN="${SATYS_PYTHON:-/data/satys/venv/bin/python}"
cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export SATYS_HEADLESS=True
export SATYS_LOCK_DIR="${SATYS_LOCK_DIR:-$PROJECT_DIR/.lock}"
exec "$PYTHON_BIN" automatizar_registros_diario.py \
  --python "$PYTHON_BIN" \
  --headless \
  --workers "${SATYS_WORKERS:-10}" \
  --timeout-registro "${SATYS_TIMEOUT_REGISTRO:-900}" \
  --reintentos-registro "${SATYS_REINTENTOS_REGISTRO:-3}" \
  --workers-reintento "${SATYS_WORKERS_REINTENTO:-3}" \
  --estado-json "$PROJECT_DIR/logs/estado_actual.json" \
  --sin-notificacion
