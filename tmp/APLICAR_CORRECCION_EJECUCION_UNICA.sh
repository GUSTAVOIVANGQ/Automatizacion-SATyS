#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec sudo bash "$ROOT/Automatizacion-SATyS/scripts/aplicar_correccion_ejecucion_unica.sh" "${1:-/data/gustavo.garcia/satys/Automatizacion-SATyS}"
