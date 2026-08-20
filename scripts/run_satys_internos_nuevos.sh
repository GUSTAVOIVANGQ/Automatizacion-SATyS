#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SATYS_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON_BIN="${SATYS_PYTHON:-$(cd "$PROJECT_DIR/.." && pwd)/venv/bin/python}"

cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export SATYS_LOCK_DIR="${SATYS_LOCK_DIR:-$PROJECT_DIR/.lock}"
INTERNOS_WORKERS="${SATYS_INTERNOS_WORKERS:-12}"

if ! [[ "$INTERNOS_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "SATYS_INTERNOS_WORKERS debe ser un entero positivo." >&2
  exit 2
fi

ARGS=(
  automatizar_registros_diario.py
  --python "$PYTHON_BIN"
  --solo-internos
  --internos-workers "$INTERNOS_WORKERS"
  --estado-json "$PROJECT_DIR/logs/estado_actual.json"
  --sin-notificacion
)
if [[ "${SATYS_VISIBLE:-0}" == "1" ]]; then
  ARGS+=(--visible)
else
  ARGS+=(--headless)
fi
if [[ "${SATYS_SIN_EMAIL:-0}" == "1" ]]; then
  ARGS+=(--sin-email)
fi

echo "SATyS Internos nuevos: seis bandejas, $INTERNOS_WORKERS navegadores configurados."
exec "$PYTHON_BIN" "${ARGS[@]}"
