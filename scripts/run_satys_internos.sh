#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SATYS_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PYTHON_BIN="${SATYS_PYTHON:-$(cd "$PROJECT_DIR/.." && pwd)/venv/bin/python}"

cd "$PROJECT_DIR"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export SATYS_LOCK_DIR="${SATYS_LOCK_DIR:-$PROJECT_DIR/.lock}"
INTERNOS_WORKERS="${SATYS_INTERNOS_WORKERS:-6}"

if ! [[ "$INTERNOS_WORKERS" =~ ^[0-6]$ ]]; then
  echo "SATYS_INTERNOS_WORKERS debe ser un entero entre 0 y 6." >&2
  exit 2
fi

ARGS=(main_procesar.py --todos-internos --internos-workers "$INTERNOS_WORKERS")
if [[ "${SATYS_VISIBLE:-0}" != "1" ]]; then
  ARGS+=(--headless)
fi
if [[ "${SATYS_SIN_EMAIL:-0}" == "1" ]]; then
  ARGS+=(--sin-email)
fi

echo "SATyS Internos: seis bandejas, navegadores paralelos: $INTERNOS_WORKERS."
exec "$PYTHON_BIN" "${ARGS[@]}"
