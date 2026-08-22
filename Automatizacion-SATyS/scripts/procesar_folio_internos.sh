#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SATYS_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON_BIN="${SATYS_PYTHON:-$(cd "$PROJECT_DIR/.." && pwd)/venv/bin/python}"
FOLIO="${1:-}"
INTERNOS_WORKERS="${SATYS_INTERNOS_WORKERS:-1}"

if ! [[ "$FOLIO" =~ ^[0-9]{1,15}$ ]]; then
  echo "Uso: bash scripts/procesar_folio_internos.sh FOLIO" >&2
  echo "FOLIO debe contener entre 1 y 15 dígitos." >&2
  exit 2
fi
if ! [[ "$INTERNOS_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
  echo "SATYS_INTERNOS_WORKERS debe ser un entero positivo." >&2
  exit 2
fi

cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export SATYS_LOCK_DIR="${SATYS_LOCK_DIR:-$PROJECT_DIR/.lock}"

ARGS=(
  automatizar_registros_diario.py
  --python "$PYTHON_BIN"
  --folio-internos "$FOLIO"
  --internos-workers "$INTERNOS_WORKERS"
  --sin-email
  --sin-notificacion
  --estado-json "$PROJECT_DIR/logs/estado_actual.json"
)
if [[ "${SATYS_VISIBLE:-0}" == "1" ]]; then
  ARGS+=(--visible)
else
  ARGS+=(--headless)
fi

echo "SATyS: procesamiento completo del Folio Internos $FOLIO (correo deshabilitado)."
exec "$PYTHON_BIN" "${ARGS[@]}"
